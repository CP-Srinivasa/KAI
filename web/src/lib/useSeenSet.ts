import { useCallback, useRef } from "react";

export interface SeenSet<K> {
  has: (key: K) => boolean;
  add: (key: K) => void;
  seed: (keys: Iterable<K>) => void;
}

export function useSeenSet<K>(cap: number): SeenSet<K> {
  const setRef = useRef<Set<K>>(new Set());
  const orderRef = useRef<K[]>([]);

  const has = useCallback((key: K) => setRef.current.has(key), []);

  const add = useCallback(
    (key: K) => {
      const set = setRef.current;
      const order = orderRef.current;
      if (set.has(key)) return;
      set.add(key);
      order.push(key);
      while (order.length > cap) {
        const evicted = order.shift();
        if (evicted !== undefined) set.delete(evicted);
      }
    },
    [cap],
  );

  const seed = useCallback(
    (keys: Iterable<K>) => {
      const set = setRef.current;
      const order = orderRef.current;
      for (const key of keys) {
        if (set.has(key)) continue;
        set.add(key);
        order.push(key);
      }
      while (order.length > cap) {
        const evicted = order.shift();
        if (evicted !== undefined) set.delete(evicted);
      }
    },
    [cap],
  );

  return { has, add, seed };
}
