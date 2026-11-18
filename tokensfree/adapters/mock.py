"""In-process mock adapter. No sockets, no prompts retained, no billing."""
from tokensfree.adapters.base import Admission, ChatRequest, ChatResult


class MockChatAdapter:
    capabilities = frozenset({"text"})

    def __init__(self, name, content="mock response", error=None):
        self.name = name
        self.content = content
        self.error = error
        self.calls = 0

    async def complete(self, request, admission):
        if not isinstance(request, ChatRequest):
            raise TypeError("request must be a ChatRequest")
        if not isinstance(admission, Admission):
            raise TypeError("admission must be issued by the router")
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ChatResult(content=self.content, model=request.model)
