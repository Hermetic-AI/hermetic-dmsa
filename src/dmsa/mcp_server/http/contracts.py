"""HTTP 请求模型与安全规范化。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from dmsa.memory import CodingMemoryValidationError, SourceMessage


class MessageContract(BaseModel):
    """来源消息，仅接受文本和可选毫秒时间戳。"""

    model_config = ConfigDict(extra="forbid")
    role: StrictStr
    content: StrictStr | list[dict[str, StrictStr]]
    timestamp: StrictInt | None = Field(default=None, ge=0)

    def source(self) -> SourceMessage:
        """转换为公开 memory API 的消息对象。"""
        if isinstance(self.content, str):
            content = self.content
        else:
            parts: list[str] = []
            for part in self.content:
                if set(part) != {"type", "text"} or part.get("type") != "text":
                    raise CodingMemoryValidationError("only text content parts are supported")
                parts.append(part["text"])
            content = "".join(parts)
        return SourceMessage(self.role, content, self.timestamp)


class AddContract(BaseModel):
    """原子写入请求。"""

    model_config = ConfigDict(extra="forbid")
    request_id: StrictStr
    messages: list[MessageContract] = Field(min_length=1)
    user_id: StrictStr
    session_id: StrictStr
    repo: StrictStr = ""


class SearchContract(BaseModel):
    """隔离在 user scope 内的检索请求。"""

    model_config = ConfigDict(extra="forbid")
    query: StrictStr
    user_id: StrictStr
    top_k: StrictInt = Field(default=5, ge=1)
    task_id: StrictStr | None = None
    repo: StrictStr = ""
    options: list[StrictStr] = Field(default_factory=list)

    @field_validator("options")
    @classmethod
    def no_unknown_options(cls, value: list[str]) -> list[str]:
        """兼容空 options，拒绝尚未实现的检索选项。"""
        if value:
            raise ValueError("nonempty options are unsupported")
        return value


def validate_message(contract: MessageContract) -> SourceMessage:
    """将领域校验错误统一为 HTTP contract 校验错误。"""
    try:
        return contract.source()
    except CodingMemoryValidationError as exc:
        raise CodingMemoryValidationError("invalid message") from exc
