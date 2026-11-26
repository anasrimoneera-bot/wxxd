"""微信小店 消息推送 (微信小店后台 → 开发 → 消息推送): signature check, decryption (安全模式) and parsing.

WeChat calls the URL with ?signature&timestamp&nonce (plain) or ?msg_signature&timestamp&nonce (安全模式,
the body carries "Encrypt"). Signature = sha1 of the sorted [token, timestamp, nonce(, encrypt)].
Encrypt = base64(AES-256-CBC(random 16 bytes + 4-byte length + message + appid)), key = base64 of
EncodingAESKey + "=", iv = the key's first 16 bytes, PKCS#7 padding to 32 bytes. Data format JSON or XML.
"""
import base64
import hashlib
import json
import struct
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class PushError(Exception):
    pass


def signature(token: str, timestamp: str, nonce: str, encrypt: str = "") -> str:
    parts = [token, timestamp, nonce] + ([encrypt] if encrypt else [])
    return hashlib.sha1("".join(sorted(parts)).encode()).hexdigest()


def _key(aes_key: str) -> bytes:
    key = base64.b64decode(aes_key + "=")
    if len(key) != 32:
        raise PushError("EncodingAESKey 应为 43 位")
    return key


def decrypt(aes_key: str, encrypt: str) -> str:
    key = _key(aes_key)
    d = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
    plain = d.update(base64.b64decode(encrypt)) + d.finalize()
    pad = plain[-1]
    if not 1 <= pad <= 32:
        raise PushError("消息解密失败")
    plain = plain[:-pad]
    n = struct.unpack(">I", plain[16:20])[0]
    return plain[20:20 + n].decode("utf-8")


def encrypt(aes_key: str, text: str, appid: str, random16: bytes | None = None) -> str:  # for tests
    import os
    key = _key(aes_key)
    body = (random16 or os.urandom(16)) + struct.pack(">I", len(text.encode())) + text.encode() + appid.encode()
    pad = 32 - len(body) % 32
    body += bytes([pad]) * pad
    e = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(e.update(body) + e.finalize()).decode()


def parse(text: str) -> dict:
    """JSON or XML -> dict (XML: child elements become keys, nested elements nested dicts)."""
    text = (text or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        return json.loads(text)

    def node(el):
        kids = list(el)
        if not kids:
            return (el.text or "").strip()
        return {k.tag: node(k) for k in kids}
    return node(ET.fromstring(text))


def open_message(token: str, aes_key: str, args: dict, body: str) -> dict:
    """Check and open one push. Raises PushError when it does not come from WeChat."""
    data = parse(body)
    enc = data.get("Encrypt") if isinstance(data, dict) else None
    if enc:
        if signature(token, args.get("timestamp", ""), args.get("nonce", ""), enc) != args.get("msg_signature"):
            raise PushError("签名不对")
        if not aes_key:
            raise PushError("没有设置 EncodingAESKey")
        return parse(decrypt(aes_key, enc))
    if signature(token, args.get("timestamp", ""), args.get("nonce", "")) != args.get("signature"):
        raise PushError("签名不对")
    return data


def message_text(data: dict) -> tuple[str, str]:
    """(msg_type, readable content) of a commkf_send_msg_to_kf event. Field names inside the
    message objects are not all documented: text is looked up under several names. VERIFY."""
    ev = data.get("msg_event")
    ev = " ".join(ev) if isinstance(ev, dict) else str(ev or "")
    if "EVALUATE" in ev.upper() or (isinstance(data.get("evaluate_result"), dict) and not data.get("text")):
        r = data.get("evaluate_result") or {}
        if isinstance(r, str):
            try:
                r = json.loads(r)
            except ValueError:
                r = {}
        value = {1: "不够好", 2: "一般般", 3: "挺好的"}.get(int(r.get("value") or 0), "")
        reasons = [str(x.get("text") or x.get("reason") or x) if isinstance(x, dict) else str(x) for x in r.get("reason_list") or []]
        return "evaluate", f"[买家评价客服] {value or '已评价'}" + (f"（{'、'.join(reasons)}）" if reasons else "")
    mt = data.get("msg_type")
    if isinstance(mt, dict):  # documented as an object, e.g. {"text": {}}: its key is the type
        t = next(iter(mt), "text")
    elif mt:
        t = str(mt)
    else:  # no msg_type: the message object that is present
        t = next((k for k in ("text", "image", "video", "voice", "file", "product_share", "order_share", "reference")
                  if k in data), "text")
    obj = data.get(t)
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError:
            return t, obj
    if t == "text":
        if isinstance(obj, dict):
            return t, str(obj.get("content") or obj.get("text") or obj.get("Content") or json.dumps(obj, ensure_ascii=False))
        return t, str(data.get("Content") or data.get("content") or "")
    if t == "voice" and isinstance(obj, dict) and obj.get("translation_succ") and obj.get("translated_msg"):
        return t, f"[语音] {obj['translated_msg']}"
    if t == "reference" and isinstance(obj, dict):
        ref = obj.get("ref_msg") or {}
        return t, f"{obj.get('content', '')}\n[引用] {ref.get('content', '')}".strip()
    label = {"image": "图片", "video": "视频", "voice": "语音", "file": "文件", "product_share": "商品卡片",
             "order_share": "订单卡片"}.get(t, t)
    detail = ""
    if isinstance(obj, dict):
        for k in ("product_id", "order_id", "cos_url", "title", "file_name", "url", "media_id"):
            if obj.get(k):
                detail = f" {k}={obj[k]}"
                break
    return t, f"[{label}]{detail}"
