'use client';

import { useEffect, useState } from 'react';
import { parseSnapshotTime, relativeAge } from './time';

interface RelativeAgeProps {
  /** ISO timestamp from the snapshot. */
  iso: string;
  /** Text placed before the age, e.g. " · ". Rendered only with the age. */
  prefix?: string;
  className?: string;
}

// "5d ago" depends on the reader's clock, and the brief is prerendered at
// build time, so the age is filled in after mount. Before that, and on paper,
// the absolute date next to it carries the meaning.
export default function RelativeAge({ iso, prefix = '', className }: RelativeAgeProps) {
  const [label, setLabel] = useState<string | null>(null);

  useEffect(() => {
    setLabel(relativeAge(parseSnapshotTime(iso)));
  }, [iso]);

  if (label === null) return null;
  return (
    <span className={className}>
      {prefix}
      {label}
    </span>
  );
}
