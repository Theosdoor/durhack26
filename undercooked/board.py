"""The shared message board: posts, per-agent read state and text formatting.

A post is on the board from the moment it lands (in a round: when the posting decision lands;
in chat: at once). The board an agent reads is the current round's: the chat before round r
and round r share `round == r` (see replay.py), so `read_board()` in round r returns the
chat-r and round-r posts. Older rounds stay in each agent's own conversation.
"""

from __future__ import annotations

from undercooked.replay import Post

MAX_POST_CHARS = 200


class Board:
    def __init__(self, agents: list[str]):
        self.agents = list(agents)
        self.posts: list[Post] = []
        self._read: dict[str, set[int]] = {a: set() for a in agents}   # indices into posts

    def add(self, post: Post) -> None:
        self._read[post.agent].add(len(self.posts))   # you have read your own post
        self.posts.append(post)

    def _scope(self, round: int, upto: int | None = None) -> list[int]:
        posts = self.posts if upto is None else self.posts[:upto]
        return [i for i, p in enumerate(posts) if p.round == round]

    def unread(self, agent: str, round: int) -> list[Post]:
        return [self.posts[i] for i in self._scope(round) if i not in self._read[agent]]

    def mark_read(self, agent: str, round: int) -> list[Post]:
        """Mark this round's posts read; return the ones that were unread."""
        new = self.unread(agent, round)
        self._read[agent].update(self._scope(round))
        return new

    def read_all(self, agent: str, round: int, upto: int | None = None) -> list[Post]:
        """All of this round's posts, oldest first (newest last); marks them read.

        `upto`: only the first `upto` posts on the board count (the board as it was when a
        decision started; later posts may land while its model call is still running)."""
        scope = self._scope(round, upto)
        self._read[agent].update(scope)
        return [self.posts[i] for i in scope]

    def count(self, agent: str) -> int:
        return sum(1 for p in self.posts if p.agent == agent)


def format_post(p: Post) -> str:
    when = "chat" if p.phase == "chat" else f"t={p.t}s"
    return f"[{when}] {p.agent}: {p.text}"


def format_posts(posts: list[Post], empty: str = "(no posts)") -> str:
    return "\n".join(format_post(p) for p in posts) if posts else empty
