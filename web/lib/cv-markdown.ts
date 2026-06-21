export type CvSection = { name: string; body: string };
export type ParsedCv = { header: string; sections: CvSection[] };

/**
 * Split a master_cv.md document into the leading header block (name/contacts,
 * everything before the first "## ") and an ordered list of "## " sections.
 * "### " sub-headings inside a section body are left untouched.
 */
export function parseCvMarkdown(markdown: string): ParsedCv {
  const lines = markdown.split("\n");
  const headerLines: string[] = [];
  const sections: CvSection[] = [];
  let current: { name: string; bodyLines: string[] } | null = null;

  for (const line of lines) {
    const match = /^##\s+(.*)$/.exec(line);
    if (match) {
      if (current) {
        sections.push({
          name: current.name,
          body: current.bodyLines.join("\n").trim(),
        });
      }
      current = { name: (match[1] ?? "").trim(), bodyLines: [] };
    } else if (current) {
      current.bodyLines.push(line);
    } else {
      headerLines.push(line);
    }
  }
  if (current) {
    sections.push({
      name: current.name,
      body: current.bodyLines.join("\n").trim(),
    });
  }

  return { header: headerLines.join("\n").trim(), sections };
}

/**
 * Reassemble header + sections back into a single markdown document, preserving
 * the original section order (and any sections we didn't recognize by name).
 */
export function buildCvMarkdown(header: string, sections: CvSection[]): string {
  const parts: string[] = [];
  const trimmedHeader = header.trim();
  if (trimmedHeader) {
    parts.push(trimmedHeader);
  }
  for (const section of sections) {
    parts.push(`## ${section.name}\n${section.body.trim()}`);
  }
  return parts.join("\n\n").trim() + "\n";
}
