from .protocol import OpCode, ResponseCode


class AVSSError(Exception):
    """Base class for exceptions raised by AVSSClient."""


class AVSSConnectionError(AVSSError):
    """Raised when the AVSS connection is lost or disconnected."""


class AVSSTransportError(AVSSError):
    """Raised when there has been an error in the underlying transport."""


class AVSSProtocolError(AVSSError):
    """Raised when a protocol violation has occurred."""

    @staticmethod
    def unexpected_response(
        request_opcode: OpCode,
        response_opcode: OpCode,
        expected: OpCode | set[OpCode] | None = None,
    ) -> "AVSSProtocolError":
        """Create error for unexpected response opcode.

        Args:
            request_opcode: The request that was sent
            response_opcode: The response that was received
            expected: Optional expected opcode(s) for more specific message

        Returns:
            AVSSProtocolError instance with descriptive message
        """
        if expected is not None:
            if isinstance(expected, set):
                expected_names = ", ".join(sorted(op.name for op in expected))
                msg = (
                    f"Unexpected response for {request_opcode.name}: "
                    f"received {response_opcode.name}, expected one of [{expected_names}]"
                )
            else:
                msg = (
                    f"Unexpected response for {request_opcode.name}: "
                    f"received {response_opcode.name}, expected {expected.name}"
                )
        else:
            msg = (
                f"Unexpected response for {request_opcode.name}: {response_opcode.name}"
            )

        return AVSSProtocolError(msg)


class AVSSControlPointError(AVSSError):
    """Raised when an AVSS control point operation returns an error."""

    def __init__(
        self,
        message: str,
        response_code: int | ResponseCode,
        opcode: int | OpCode,
    ):
        super().__init__(message)
        self.response_code = response_code
        self.opcode = opcode

    @staticmethod
    def from_response(
        rc: int | ResponseCode, opcode: OpCode
    ) -> "AVSSControlPointError":
        """Create exception from a generic error response.

        Args:
            rc: Response code (int or ResponseCode enum member)
            opcode: The operation that failed (for context)

        Returns:
            AVSSControlPointError or an appropriate sub-class exception instance
            with an descriptive message in addition to response_code and opcode.

        Raises:
            ValueError: If rc is ResponseCode.OK (not an error)
        """
        # Handle raw integers (for newer/unknown codes)
        if not isinstance(rc, ResponseCode):
            if rc in ResponseCode:
                rc = ResponseCode(rc)
            else:
                return AVSSControlPointError(
                    f"Device returned response code {rc} (firmware may be newer than client)",
                    response_code=rc,
                    opcode=opcode,
                )

        if rc == ResponseCode.OK:
            raise ValueError("ResponseCode.OK is not an error response code")

        # Map specific codes to dedicated exception types
        if rc == ResponseCode.OPCODE_UNSUPPORTED:
            return AVSSOpCodeUnsupportedError(opcode=opcode, response_code=rc)
        elif rc == ResponseCode.BAD_ARGUMENT:
            return AVSSBadArgumentError(opcode=opcode, response_code=rc)

        # All other codes use generic error with descriptive message
        messages = {
            ResponseCode.ERROR: "Operation failed",
            ResponseCode.BUSY: "Device is busy",
            ResponseCode.UNEXPECTED: "Unexpected error occurred",
            # NOTE: Some unused response code intentionally omitted.
        }

        message = messages.get(rc, None)
        if message is None:
            message = f"Operation failed with response code {rc}"

        return AVSSControlPointError(message, response_code=rc, opcode=opcode)


class AVSSOpCodeUnsupportedError(AVSSControlPointError):
    """Raised when an operation is not supported by the node firmware."""

    def __init__(self, opcode: OpCode, response_code: int | ResponseCode):
        try:
            op_name = OpCode(opcode).name
            msg = f"Operation {op_name} not supported by node"
        except ValueError:
            msg = f"Operation {opcode} not supported by node"

        super().__init__(msg, response_code=response_code, opcode=opcode)


class AVSSBadArgumentError(AVSSControlPointError):
    """Raised when an invalid argument is provided to a control point operation."""

    def __init__(self, opcode: int | OpCode, response_code: int | ResponseCode):
        try:
            op_name = OpCode(opcode).name
            msg = f"Operation {op_name} not supported by node"
        except ValueError:
            msg = f"Operation {opcode} not supported by node"

        super().__init__(msg, response_code=response_code, opcode=opcode)
