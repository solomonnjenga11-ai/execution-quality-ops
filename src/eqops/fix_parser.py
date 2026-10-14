"""FIX 4.2/4.4 tag=value parsing and validation.

Real FIX uses the SOH character (\\x01) as the field delimiter. Logs are usually
stored with SOH replaced by '|' so humans can read them; this parser accepts
either and always computes BodyLength(9) and CheckSum(10) on the SOH form,
which is what the counterparty's engine would have checked.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SOH = "\x01"

TAGS = {
    "8": "BeginString", "9": "BodyLength", "35": "MsgType", "49": "SenderCompID",
    "56": "TargetCompID", "34": "MsgSeqNum", "52": "SendingTime", "11": "ClOrdID",
    "37": "OrderID", "17": "ExecID", "150": "ExecType", "39": "OrdStatus", "55": "Symbol",
    "54": "Side", "38": "OrderQty", "40": "OrdType", "44": "Price", "32": "LastQty",
    "31": "LastPx", "14": "CumQty", "151": "LeavesQty", "6": "AvgPx", "60": "TransactTime",
    "58": "Text", "10": "CheckSum",
}
MSG_TYPES = {"D": "NewOrderSingle", "8": "ExecutionReport", "F": "OrderCancelRequest",
             "0": "Heartbeat", "A": "Logon"}
EXEC_TYPES = {"0": "NEW", "F": "TRADE", "4": "CANCELED", "8": "REJECTED", "1": "PARTIAL_FILL",
              "2": "FILL"}
SIDES = {"1": "BUY", "2": "SELL"}


@dataclass
class FixMessage:
    raw: str
    fields: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def msg_type(self) -> str | None:
        return self.fields.get("35")

    def get(self, tag: str, default=None):
        return self.fields.get(tag, default)


def checksum(body_with_soh: str) -> str:
    """Sum of bytes up to and including the SOH before tag 10, mod 256, 3 digits."""
    return f"{sum(body_with_soh.encode('ascii')) % 256:03d}"


def body_length(msg_soh: str) -> int:
    """Bytes after the '9=...<SOH>' field up to and including the SOH before '10='."""
    start = msg_soh.index(SOH, msg_soh.index(SOH) + 1) + 1    # after 8= and 9= fields
    end = msg_soh.rindex(SOH + "10=") + 1
    return end - start


def build(fields: list[tuple[str, str]], begin: str = "FIX.4.4") -> str:
    """Assemble a message with correct BodyLength and CheckSum (returned '|'-delimited)."""
    body = "".join(f"{t}={v}{SOH}" for t, v in fields)
    head = f"8={begin}{SOH}9={len(body.encode('ascii'))}{SOH}"
    msg = head + body
    return (msg + f"10={checksum(msg)}{SOH}").replace(SOH, "|")


def parse(line: str) -> FixMessage:
    line = line.strip()
    soh = line.replace("|", SOH)
    if not soh.endswith(SOH):
        soh += SOH
    msg = FixMessage(raw=line)
    for part in soh.split(SOH):
        if not part:
            continue
        if "=" not in part:
            msg.errors.append(f"MALFORMED_FIELD:{part[:20]}")
            continue
        tag, val = part.split("=", 1)
        if tag in msg.fields:
            msg.errors.append(f"DUPLICATE_TAG:{tag}")
        msg.fields[tag] = val

    for tag in ("8", "9", "35", "10"):
        if tag not in msg.fields:
            msg.errors.append(f"MISSING_TAG:{tag}")
    if "10" in msg.fields and "8" in msg.fields:
        cut = soh.rindex(SOH + "10=") + 1
        if checksum(soh[:cut]) != msg.fields["10"]:
            msg.errors.append("BAD_CHECKSUM")
        try:
            if int(msg.fields.get("9", -1)) != body_length(soh):
                msg.errors.append("BAD_BODY_LENGTH")
        except ValueError:
            msg.errors.append("BAD_BODY_LENGTH")
    required = {"D": ["11", "55", "54", "38", "40"], "8": ["11", "37", "17", "150", "39", "55", "54"]}
    for tag in required.get(msg.msg_type, []):
        if tag not in msg.fields:
            msg.errors.append(f"MISSING_TAG:{tag}")
    return msg


def to_records(lines) -> list[dict]:
    """Flatten parsed messages into rows ready for a DataFrame / SQL table."""
    rows = []
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        m = parse(line)
        f = m.fields
        rows.append({
            "line_no": i, "msg_type": f.get("35"), "seq_num": _int(f.get("34")),
            "sending_time": f.get("52"), "transact_time": f.get("60"),
            "cl_ord_id": f.get("11"), "order_id": f.get("37"), "exec_id": f.get("17"),
            "exec_type": f.get("150"), "ord_status": f.get("39"), "symbol": f.get("55"),
            "side": SIDES.get(f.get("54"), f.get("54")), "order_qty": _num(f.get("38")),
            "ord_type": f.get("40"), "limit_px": _num(f.get("44")), "last_qty": _num(f.get("32")),
            "last_px": _num(f.get("31")), "cum_qty": _num(f.get("14")),
            "leaves_qty": _num(f.get("151")), "avg_px": _num(f.get("6")), "text": f.get("58"),
            "parse_errors": ";".join(m.errors) or None,
        })
    return rows


def _num(v):
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _int(v):
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None
