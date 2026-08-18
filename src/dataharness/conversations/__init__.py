"""Chat-first conversation services.

Conversation is the user-facing interaction boundary.  It deliberately does not
create a Task or Run for an ordinary message; those records remain the durable
boundary for an explicitly requested, long-running analysis job.
"""

from .service import ConversationAgentService, ConversationResponse

__all__ = ["ConversationAgentService", "ConversationResponse"]
