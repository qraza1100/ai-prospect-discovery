from __future__ import annotations

from collections import defaultdict

from app.schemas import Platform, ProfileCandidate
from app.utils.normalizers import name_key, name_similarity, normalize_handle

MIN_HANDLE_LENGTH = 4
NAME_MATCH_THRESHOLD = 0.8
_SHARED_HANDLE_PLATFORMS = frozenset({Platform.INSTAGRAM, Platform.THREADS})


class _Clusters:
    """Union-find that refuses merges which would put two different accounts from the same
    platform into one cluster (two LinkedIn profiles are two people, not one)."""

    def __init__(self, candidates: list[ProfileCandidate]) -> None:
        self._parent = list(range(len(candidates)))
        self._urls: list[dict[Platform, set[str]]] = [{c.platform: {c.url}} for c in candidates]

    def find(self, i: int) -> int:
        while self._parent[i] != i:
            self._parent[i] = self._parent[self._parent[i]]
            i = self._parent[i]
        return i

    def conflicts(self, i: int, j: int) -> bool:
        a, b = self._urls[self.find(i)], self._urls[self.find(j)]
        return any(p in b and a[p] != b[p] for p in a)

    def union(self, i: int, j: int, *, force: bool = False) -> bool:
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return True
        if not force and self.conflicts(ri, rj):
            return False
        self._parent[rj] = ri
        for platform, urls in self._urls[rj].items():
            self._urls[ri].setdefault(platform, set()).update(urls)
        return True


def same_identity(a: ProfileCandidate, b: ProfileCandidate) -> bool:
    """Pairwise rule for profiles found independently on two platforms.

    A shared handle alone is not enough (handles collide across platforms) and a shared
    name alone is never enough (namesakes are common). Both must agree, except for
    Instagram and Threads, which share one account namespace by design.
    """
    if a.platform == b.platform:
        return False
    handle_a, handle_b = normalize_handle(a.handle), normalize_handle(b.handle)
    if len(handle_a) < MIN_HANDLE_LENGTH or handle_a != handle_b:
        return False
    if {a.platform, b.platform} == _SHARED_HANDLE_PLATFORMS:
        return True
    if name_similarity(a.name, b.name) >= NAME_MATCH_THRESHOLD:
        return True
    names = [name_key(n.lstrip("@")) for n in (a.name, b.name) if n]
    return bool(names) and all(n.replace(" ", "") == handle_a for n in names)


class DeduplicationService:
    def cluster(self, candidates: list[ProfileCandidate]) -> list[list[ProfileCandidate]]:
        clusters = _Clusters(candidates)
        index_by_url = {c.url: i for i, c in enumerate(candidates)}

        for i, candidate in enumerate(candidates):
            for url in candidate.linked_urls:
                j = index_by_url.get(url)
                if j is not None:
                    clusters.union(i, j, force=True)

        owners: dict[str, list[int]] = defaultdict(list)
        for i, candidate in enumerate(candidates):
            for key in candidate.contacts.identity_keys():
                owners[key].append(i)
        for indexes in owners.values():
            for j in indexes[1:]:
                clusters.union(indexes[0], j, force=True)

        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                if same_identity(candidates[i], candidates[j]):
                    clusters.union(i, j)

        grouped: dict[int, list[ProfileCandidate]] = defaultdict(list)
        for i, candidate in enumerate(candidates):
            grouped[clusters.find(i)].append(candidate)
        return list(grouped.values())

    @staticmethod
    def can_join(cluster: list[ProfileCandidate], candidate: ProfileCandidate) -> bool:
        if any(m.platform == candidate.platform for m in cluster):
            return False
        return any(same_identity(m, candidate) for m in cluster)
