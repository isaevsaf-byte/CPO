'use client';

import { useEffect, useRef, useState } from 'react';

// Clipboard API where the browser allows it. Returns false rather than
// throwing, so callers can fall back to selecting the text.
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Denied, or not a secure context. Fall through.
  }
  return false;
}

type CopyState = 'idle' | 'copied' | 'selected';

interface CopyBoxProps {
  /** The exact text to show and copy. */
  text: string;
  /** Visible label above the box. */
  label?: string;
  /** Height of the box in text rows. */
  rows?: number;
  /** Show the "Copied" confirmation from the start, when the caller has
   *  already put the text on the clipboard. */
  initiallyCopied?: boolean;
}

export default function CopyBox({ text, label = 'Request text', rows = 12, initiallyCopied = false }: CopyBoxProps) {
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const [state, setState] = useState<CopyState>(initiallyCopied ? 'copied' : 'idle');
  const firstText = useRef(text);

  // A "Copied" label that outlives an edit would be vouching for old text.
  useEffect(() => {
    if (text !== firstText.current) {
      firstText.current = text;
      setState('idle');
    }
  }, [text]);

  const selectAll = (): boolean => {
    const box = boxRef.current;
    if (!box) return false;
    box.focus();
    box.select();
    // iOS Safari ignores select() on its own.
    box.setSelectionRange(0, box.value.length);
    return true;
  };

  const handleCopy = async () => {
    if (await copyText(text)) {
      setState('copied');
      return;
    }
    // Older browsers and locked-down clipboards: select the text and try the
    // legacy copy command; if that is blocked too, leave it selected so one
    // keyboard shortcut finishes the job.
    if (!selectAll()) return;
    let copied = false;
    try {
      copied = document.execCommand('copy');
    } catch {
      copied = false;
    }
    setState(copied ? 'copied' : 'selected');
  };

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <label htmlFor="scan-copy-box" className="text-sm font-semibold text-gray-700">
          {label}
        </label>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-500" aria-live="polite">
            {state === 'copied' && 'Copied to your clipboard.'}
            {state === 'selected' && 'Selected. Press Ctrl+C (⌘C on a Mac) to copy.'}
          </span>
          <button
            type="button"
            onClick={handleCopy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-semibold text-gray-800 hover:bg-gray-50 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
            {state === 'copied' ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>
      <textarea
        id="scan-copy-box"
        ref={boxRef}
        readOnly
        value={text}
        rows={rows}
        onFocus={(event) => event.currentTarget.select()}
        className="w-full rounded-lg border border-gray-200 bg-slate-50 p-3 font-mono text-xs sm:text-sm leading-relaxed text-gray-800 focus:outline-none focus:ring-2 focus:ring-blue-800"
      />
    </div>
  );
}
