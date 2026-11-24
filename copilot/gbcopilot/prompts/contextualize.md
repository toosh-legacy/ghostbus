---
name: contextualize
version: 1
---
Rewrite the user's latest message as one standalone question about MBTA bus reliability, using the earlier turns
only to fill in what the latest message leaves out (route, metric, month, time band). If it is already standalone,
return it unchanged. Never add facts that are not in the conversation.

Conversation so far:
$history

Latest message: $question
