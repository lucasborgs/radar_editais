// Geração de DOCX client-side a partir das seções da proposta (spec §F5/W8).
// Conversão de markdown BÁSICO (bold/itálico/listas) — sem perfeccionismo:
// o objetivo é um .docx editável, não fidelidade total ao markdown.

import {
  Document,
  Packer,
  Paragraph,
  HeadingLevel,
  TextRun,
} from "docx";
import type { WorkspaceSection } from "./types";

// Quebra uma linha de markdown inline em runs (negrito **/__, itálico */_).
// Heurística simples: alterna pares de marcadores. Marcadores órfãos ficam
// como texto literal.
function inlineRuns(line: string): TextRun[] {
  const runs: TextRun[] = [];
  // Tokeniza por **…**, __…__, *…*, _…_ preservando o texto entre eles.
  const re = /(\*\*|__)(.+?)\1|(\*|_)(.+?)\3/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(line)) !== null) {
    if (m.index > last) {
      runs.push(new TextRun(line.slice(last, m.index)));
    }
    if (m[2] !== undefined) {
      runs.push(new TextRun({ text: m[2], bold: true }));
    } else if (m[4] !== undefined) {
      runs.push(new TextRun({ text: m[4], italics: true }));
    }
    last = re.lastIndex;
  }
  if (last < line.length) {
    runs.push(new TextRun(line.slice(last)));
  }
  return runs.length > 0 ? runs : [new TextRun(line)];
}

// Converte o corpo (markdown) de uma seção em parágrafos docx.
function bodyParagraphs(content: string): Paragraph[] {
  const out: Paragraph[] = [];
  const lines = content.split(/\r?\n/);
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      continue; // pula linhas em branco (espaçamento vem do estilo)
    }
    // Lista com bullet (-, *, +).
    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    if (bullet) {
      out.push(new Paragraph({ children: inlineRuns(bullet[1]), bullet: { level: 0 } }));
      continue;
    }
    // Lista numerada (1. , 2) ) → bullet simples (sem numeração nativa p/ simplicidade).
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (numbered) {
      out.push(new Paragraph({ children: inlineRuns(numbered[1]), bullet: { level: 0 } }));
      continue;
    }
    // Sub-cabeçalho markdown (### …) vira parágrafo em negrito.
    const sub = line.match(/^#{1,6}\s+(.*)$/);
    if (sub) {
      out.push(new Paragraph({ children: [new TextRun({ text: sub[1], bold: true })] }));
      continue;
    }
    out.push(new Paragraph({ children: inlineRuns(line) }));
  }
  return out;
}

export async function buildDocxBlob(
  title: string,
  sections: WorkspaceSection[],
): Promise<Blob> {
  const children: Paragraph[] = [
    new Paragraph({ text: title, heading: HeadingLevel.HEADING_1 }),
    new Paragraph({
      children: [
        new TextRun({
          text: new Date().toLocaleDateString("pt-BR"),
          italics: true,
          color: "666666",
        }),
      ],
    }),
  ];

  sections.forEach((s, i) => {
    children.push(
      new Paragraph({
        text: `${i + 1}. ${s.title}`,
        heading: HeadingLevel.HEADING_2,
      }),
    );
    if (s.content.trim()) {
      children.push(...bodyParagraphs(s.content));
    } else {
      children.push(
        new Paragraph({ children: [new TextRun({ text: "[A preencher]", italics: true })] }),
      );
    }
  });

  const doc = new Document({ sections: [{ children }] });
  return Packer.toBlob(doc);
}
