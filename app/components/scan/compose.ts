import type { ScanContact, ScanSupplierRow } from '../../../types/extras';

export const MAX_SCAN_ROWS = 10;

export function filledRows(rows: ScanSupplierRow[]): ScanSupplierRow[] {
  return rows.filter((row) => row.name.trim() !== '');
}

export function scanSubject(company: string): string {
  return `Supplier scan request — ${company.trim() || '(company)'}`;
}

// The same text goes into the email draft and the copy box, so what the
// visitor reads on the page is exactly what arrives.
export function scanBody(contact: ScanContact, rows: ScanSupplierRow[]): string {
  const suppliers = filledRows(rows);
  const lines: string[] = [
    'Hello Safar,',
    '',
    `Please run a free supplier scan on ${suppliers.length === 1 ? 'this supplier' : `these ${suppliers.length} suppliers`}.`,
    '',
    'Supplier — country of the supplying site',
  ];

  if (suppliers.length === 0) {
    lines.push('(no suppliers added yet)');
  } else {
    suppliers.forEach((row, idx) => {
      const country = row.country.trim() || 'country not given';
      lines.push(`${idx + 1}. ${row.name.trim()} — ${country}`);
    });
  }

  const note = contact.note.trim();
  if (note) {
    lines.push('', 'Note:', note);
  }

  lines.push(
    '',
    `Name: ${contact.name.trim()}`,
    `Company: ${contact.company.trim()}`,
    `Email: ${contact.email.trim()}`,
    '',
    'Sent from cpo-watchtower.co.uk/scan',
  );

  return lines.join('\n');
}

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export type ScanField = 'suppliers' | 'name' | 'company' | 'email';

export interface ScanProblem {
  field: ScanField;
  message: string;
  rowId?: number;
}

export function validateScan(contact: ScanContact, rows: ScanSupplierRow[]): ScanProblem[] {
  const problems: ScanProblem[] = [];

  rows.forEach((row, idx) => {
    if (row.name.trim() === '' && row.country.trim() !== '') {
      problems.push({
        field: 'suppliers',
        rowId: row.id,
        message: `Row ${idx + 1} has a country but no supplier name. Add the name or clear the row.`,
      });
    }
  });
  if (filledRows(rows).length === 0) {
    problems.push({ field: 'suppliers', message: 'Add at least one supplier.' });
  }
  if (contact.name.trim() === '') {
    problems.push({ field: 'name', message: 'Add your name.' });
  }
  if (contact.company.trim() === '') {
    problems.push({ field: 'company', message: 'Add your company.' });
  }
  if (!EMAIL_PATTERN.test(contact.email.trim())) {
    problems.push({ field: 'email', message: 'Add the email address the brief should go to.' });
  }

  return problems;
}
