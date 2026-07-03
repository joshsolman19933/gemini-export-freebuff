"""Paginált API lekérés és retry/backoff logika."""

import asyncio
import json

import orjson
from gemini_webapi import GeminiClient
from gemini_webapi.constants import GRPC
from gemini_webapi.types import ChatInfo, RPCData
from gemini_webapi.utils import extract_json_from_response, get_nested_value


async def _fetch_chats_paginated(client: GeminiClient, max_total: int) -> list[ChatInfo]:
    """Page token alapú paginációval lekéri az összes beszélgetést.

    A Gemini API ~100-as batch limitet használ. A válasz `part_body`
    struktúrája: [None, token_string, chat_list]. Az index 1-en lévő
    string a következő oldal tokenje, amit a kérés második
    paramétereként kell visszaküldeni: [100, token, [filter]].
    """
    all_chats: list[ChatInfo] = []

    # Két RPC típust használunk (mint az eredeti _fetch_recent_chats):
    # [1, None, 1] és [0, None, 1] - különböző szűrők/nézetek
    # Mindkettőt pagináljuk a teljesség érdekében.
    rpc_filters = [
        [1, None, 1],   # pinned/first view
        [0, None, 1],   # unpinned/second view
    ]

    for rpc_filter in rpc_filters:
        page_token = None
        while len(all_chats) < max_total:
            # Használjuk az orjson-t, mert a gemini_webapi is ezt használja
            # (bytes-t ad vissza, .decode("utf-8")-al stringgé alakítjuk)
            payload = orjson.dumps([100, page_token, rpc_filter]).decode("utf-8")

            try:
                response = await client._batch_execute([
                    RPCData(rpcid=GRPC.LIST_CHATS, payload=payload)
                ])
            except Exception:
                break  # Ha hibázik, lépjünk a következő filterre

            chats_json = extract_json_from_response(response.text)
            has_more = False

            for part in chats_json:
                part_body_str = get_nested_value(part, [2])
                if not part_body_str:
                    continue
                try:
                    part_body = json.loads(part_body_str)
                except json.JSONDecodeError:
                    continue

                # Chat lista kinyerése
                chat_list = get_nested_value(part_body, [2])
                if isinstance(chat_list, list):
                    for chat_data in chat_list:
                        if not isinstance(chat_data, list) or len(chat_data) < 2:
                            continue
                        cid = get_nested_value(chat_data, [0], "")
                        title = get_nested_value(chat_data, [1], "")
                        is_pinned = bool(get_nested_value(chat_data, [2]))
                        timestamp_data = get_nested_value(chat_data, [5])
                        timestamp = 0.0
                        if isinstance(timestamp_data, list) and len(timestamp_data) >= 2:
                            timestamp = float(timestamp_data[0]) + float(timestamp_data[1]) / 1e9

                        if cid and not any(c.cid == cid for c in all_chats):
                            all_chats.append(ChatInfo(
                                cid=cid, title=title,
                                is_pinned=is_pinned, timestamp=timestamp,
                            ))

                # Page token kinyerése: a part_body struktúra [None, str_token, list]
                # a token az index 1-en lévő base64-szerű string
                next_token = get_nested_value(part_body, [1])
                if isinstance(next_token, str) and next_token:
                    page_token = next_token
                    has_more = True

            if not has_more:
                break  # Nincs több oldal ennél a filternél

    return all_chats


async def _retry_read_chat(
    client: GeminiClient,
    cid: str,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> object:
    """API hívás újrapróbálása exponential backoff-fal.

    Args:
        client: GeminiClient példány
        cid: Chat ID
        max_retries: Maximális újrapróbálások száma (alap: 3)
        base_delay: Alap késleltetés másodpercben (exponenciálisan nő: 1s, 2s, 4s)

    Returns:
        A history objektum, vagy kivételt dob.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return await client.read_chat(cid)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                wait = base_delay * (2 ** attempt)
                await asyncio.sleep(wait)
    raise last_error  # type: ignore[misc]
