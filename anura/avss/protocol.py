from enum import IntEnum


class ResponseCode(IntEnum):
    """AVSS Control Point response codes."""

    RESERVED = 0
    OK = 1
    ERROR = 2
    OPCODE_UNSUPPORTED = 3
    BUSY = 4
    BAD_ARGUMENT = 5
    CONTROL_POINT_BUSY = 6
    UNEXPECTED = 7


class OpCode(IntEnum):
    """AVSS Control Point operation codes."""

    RESERVED = 0
    RESPONSE = 1
    REPORT_SNIPPETS = 2
    REPORT_AGGREGATES = 3
    REPORT_HEALTH = 4
    GET_VERSION = 5
    GET_VERSION_RESPONSE = 6
    WRITE_SETTINGS = 7
    WRITE_SETTINGS_RESPONSE = 8
    REPORT_SETTINGS = 9
    APPLY_SETTINGS = 10
    APPLY_SETTINGS_RESPONSE = 11
    TEST_THROUGHPUT = 12
    REPORT_CAPTURE = 13
    WRITE_SETTINGS_V2 = 14
    WRITE_SETTINGS_V2_RESPONSE = 15
    DEACTIVATE = 16
    TRIGGER_MEASUREMENT = 17
    GET_FIRMWARE_INFO = 18
    GET_FIRMWARE_INFO_RESPONSE = 19
    RESET_REPORT = 20
    RESET_SETTINGS = 21
    TRIGGER_CAPTURE = 22
    PREPARE_UPGRADE = 100
    APPLY_UPGRADE = 101
    CONFIRM_UPGRADE = 102
    REBOOT = 103

    @staticmethod
    def _safe_name(value: int) -> str:
        """Get opcode name if known, otherwise return formatted value.

        Args:
            value: Opcode value (int)

        Returns:
            Opcode name if value is in enum, otherwise "opcode {value}"
        """
        try:
            return OpCode(value).name
        except ValueError:
            return f"opcode {value}"


class ReportType(IntEnum):
    """AVSS Report types."""

    RESERVED = 0
    SNIPPET = 2
    AGGREGATES = 3
    HEALTH = 4
    SETTINGS = 5
    CAPTURE = 6
