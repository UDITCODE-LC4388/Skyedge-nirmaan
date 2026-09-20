import heapq

class PriorityQueue:
    def __init__(self):
        self._heap, self._n = [], 0
    def push(self, score, item):
        heapq.heappush(self._heap, (-score, self._n, item)); self._n += 1
    def pop(self):
        return heapq.heappop(self._heap)[2] if self._heap else None
    def peek(self):
        return self._heap[0] if self._heap else None
