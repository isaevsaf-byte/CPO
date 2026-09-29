import type { ReactNode } from 'react';

interface BriefSectionProps {
  title: string;
  /** Small text on the right of the title bar, e.g. the date range. */
  aside?: ReactNode;
  children: ReactNode;
}

// A card on screen; on paper, a plain block under a rule, so a printout is a
// page of text rather than a stack of boxes.
export default function BriefSection({ title, aside, children }: BriefSectionProps) {
  return (
    <section className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden break-inside-avoid print:overflow-visible print:rounded-none print:border-0 print:border-t print:border-gray-300 print:shadow-none">
      <div className="px-5 sm:px-6 py-3.5 bg-gray-50 border-b border-gray-200 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 print:bg-transparent print:border-0 print:px-0 print:pt-3 print:pb-1">
        <h2 className="text-base sm:text-lg font-bold text-gray-900">{title}</h2>
        {aside && <div className="text-xs text-gray-500">{aside}</div>}
      </div>
      <div className="px-5 sm:px-6 py-4 print:px-0 print:py-1">{children}</div>
    </section>
  );
}
