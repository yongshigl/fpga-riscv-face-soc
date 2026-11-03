"""Counter scopes for HTTP evidence: decoder counts reset on each connection.

connection_retries/decode_errors are console lifetime totals. An intentional
reconnect advances connection_generation; it does not increment either total.
"""

COUNTERS = ("connection_generation", "crc_errors", "framing_errors", "connection_retries", "decode_errors")
INTEGRITY_SCOPE = {
    "current_connection": "crc_errors and framing_errors belong to the current Board decoder and must be zero",
    "console_lifetime": "connection_retries and decode_errors may have historical counts; neither may change during verification",
    "generation": "Sustained observation keeps one generation; one requested reconnect permits exactly one increment",
    "observation": "Assertions apply to every sampled HTTP status; raw failing statuses are retained",
}


def counter_snapshot(value):
    return {key: value.get(key) for key in COUNTERS}


def checked_counters(value):
    counters = counter_snapshot(value)
    if any(type(count) is not int or count < 0 for count in counters.values()) or not counters["connection_generation"]:
        raise RuntimeError("Console generation/error counters are missing or invalid")
    return counters


def check_current_errors(value):
    if value["crc_errors"] or value["framing_errors"]:
        raise RuntimeError(f"Current connection has UART errors: CRC={value['crc_errors']} framing={value['framing_errors']}")


def begin_observation(value):
    baseline = checked_counters(value)
    if not value["connected"]:
        raise RuntimeError("Integrity observation requires a connected board")
    check_current_errors(value)
    return baseline


def check_lifetime(value, baseline):
    for key in ("connection_retries", "decode_errors"):
        if value[key] != baseline[key]:
            raise RuntimeError(f"New or reset {key}: baseline={baseline[key]} current={value[key]}")


def check_connection(value, baseline):
    current = checked_counters(value)
    check_lifetime(current, baseline)
    if current["connection_generation"] != baseline["connection_generation"]:
        raise RuntimeError(f"Unexpected connection generation: expected={baseline['connection_generation']} current={current['connection_generation']}")
    if not value["connected"]:
        raise RuntimeError("Board disconnected during the observation")
    check_current_errors(current)
    return current


def check_reconnect_transition(value, baseline):
    current = checked_counters(value)
    check_lifetime(current, baseline)
    previous, actual = baseline["connection_generation"], current["connection_generation"]
    if actual not in (previous, previous + 1):
        raise RuntimeError(f"Requested reconnect permits exactly one generation increment: previous={previous} current={actual}")
    # Disconnected statuses have no Board decoder; their zero decoder counters
    # are not treated as evidence of an established clean new connection.
    if value["connected"]:
        check_current_errors(current)
    return bool(value["connected"] and actual == previous + 1)
