const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  AlignmentType, HeadingLevel, BorderStyle, WidthType, ShadingType,
  Header, Footer, PageNumber, VerticalAlign
} = require("docx");
const fs = require("fs");

const COLORS = {
  headerBg: "1F3864",
  headerText: "FFFFFF",
  activeText: "1E7D34",
  activeBg: "E2EFDA",
  lowText: "7D4E00",
  lowBg: "FFF2CC",
  inactiveText: "C00000",
  inactiveBg: "FCE4D6",
  rowAlt: "F5F7FA",
  rowWhite: "FFFFFF",
  border: "CCCCCC",
};

function usage() {
  return "Usage: node generate-health-report.js --input <compare-or-report.json> [--output ESA-Policy-Health-Check.docx]";
}

function parseArgs() {
  const args = process.argv.slice(2);
  const out = {
    input: null,
    output: "ESA-Policy-Health-Check.docx",
  };

  for (let i = 0; i < args.length; i += 1) {
    const key = args[i];
    const value = args[i + 1];

    if (key === "--input") {
      if (!value || value.startsWith("--")) {
        throw new Error(`Missing value for --input. ${usage()}`);
      }
      out.input = value;
      i += 1;
    } else if (key === "--output") {
      if (!value || value.startsWith("--")) {
        throw new Error(`Missing value for --output. ${usage()}`);
      }
      out.output = value;
      i += 1;
    }
  }

  if (!out.input) {
    throw new Error(`Missing required --input argument. ${usage()}`);
  }

  return out;
}

function deriveStatus(hits) {
  if (hits >= 10) return "ACTIVE";
  if (hits >= 1) return "LOW";
  return "INACTIVE";
}

function deriveRecommendation(name, hits) {
  const n = String(name || "").toLowerCase();
  
  // DEFAULT policy can't be disabled
  if (n === "default") return "Keep - default policy (cannot be disabled)";
  
  if (hits >= 10) return "Keep";
  if (hits >= 1) return "Review - very low volume";
  if (n.includes("test")) return "Remove - appears to be a test policy";
  return "Safe to disable - no traffic in query period";
}

function normalizePolicyList(list) {
  const rows = Array.isArray(list) ? list : [];
  return rows
    .map((p, idx) => {
      const hits = Number(p.hits || p.hit_count || 0);
      const name = p.name || p.policy_name || p.policy_name_from_config || `Policy-${idx + 1}`;
      const status = p.status || deriveStatus(hits);
      const recommendation = p.recommendation || deriveRecommendation(name, hits);
      return {
        order: Number(p.order || idx + 1),
        name,
        hits,
        status,
        recommendation,
      };
    })
    .sort((a, b) => b.hits - a.hits || a.name.localeCompare(b.name))
    .map((p, idx) => ({ ...p, order: idx + 1 }));
}

function convertCompareToolInputToReport(input) {
  // Handle new separated incoming/outgoing format (Stage 1 v2)
  if (input.incomingPolicies || input.outgoingPolicies) {
    return {
      reportDate: input.reportDate || new Date().toISOString().slice(0, 10),
      queryPeriod: input.queryPeriod || "Config inventory vs API hit counts",
      queryParameters: input.query_parameters || {},
      incomingPolicies: Array.isArray(input.incomingPolicies) ? input.incomingPolicies : [],
      outgoingPolicies: Array.isArray(input.outgoingPolicies) ? input.outgoingPolicies : [],
      purposeText: "This report compares customer-provided ESA policy inventory against ESA Reporting API hit counts to identify active, low-traffic, and zero-hit policies.",
    };
  }

  // Handle legacy merged format (for backward compatibility)
  const withHits = Array.isArray(input.policies_with_hits) ? input.policies_with_hits : [];
  const withoutHits = Array.isArray(input.policies_without_hits) ? input.policies_without_hits : [];
  const merged = [...withHits, ...withoutHits].map((p, idx) => ({
    order: idx + 1,
    name: p.policy_name_from_config || p.matched_policy_name_from_api || `Policy-${idx + 1}`,
    hits: Number(p.hit_count || 0),
  }));

  return {
    reportDate: new Date().toISOString().slice(0, 10),
    queryPeriod: "Config inventory vs API hit counts",
    queryParameters: {},
    incomingPolicies: merged,
    outgoingPolicies: [],
    purposeText: "This report compares customer-provided ESA policy inventory against ESA Reporting API hit counts to identify active, low-traffic, and zero-hit policies.",
  };
}

function loadInput(inputPath) {
  if (!fs.existsSync(inputPath)) {
    throw new Error(`Input file not found: ${inputPath}`);
  }

  const stats = fs.statSync(inputPath);
  if (!stats.isFile()) {
    throw new Error(`Input path is not a file: ${inputPath}`);
  }

  const raw = fs.readFileSync(inputPath, "utf-8");
  if (!raw.trim()) {
    throw new Error(`Input file is empty: ${inputPath}`);
  }

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new Error(`Invalid JSON in input file ${inputPath}: ${err.message}`);
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`Invalid input format in ${inputPath}: root JSON must be an object.`);
  }

  if (parsed && (parsed.policies_with_hits || parsed.policies_without_hits)) {
    return convertCompareToolInputToReport(parsed);
  }

  const hasIncoming = Array.isArray(parsed.incomingPolicies);
  const hasOutgoing = Array.isArray(parsed.outgoingPolicies);
  if (!hasIncoming && !hasOutgoing) {
    throw new Error(
      `Invalid input schema in ${inputPath}: expected compare JSON (policies_with_hits/policies_without_hits) `
      + "or report JSON (incomingPolicies/outgoingPolicies)."
    );
  }

  return convertCompareToolInputToReport(parsed);
}

function statusColor(status) {
  if (status === "ACTIVE") return { text: COLORS.activeText, bg: COLORS.activeBg };
  if (status === "LOW") return { text: COLORS.lowText, bg: COLORS.lowBg };
  return { text: COLORS.inactiveText, bg: COLORS.inactiveBg };
}

function statusLabel(status) {
  if (status === "ACTIVE") return "ACTIVE";
  if (status === "LOW") return "LOW TRAFFIC";
  return "INACTIVE";
}

const border = { style: BorderStyle.SINGLE, size: 1, color: COLORS.border };
const borders = { top: border, bottom: border, left: border, right: border };

function headerCell(text, widthDxa) {
  return new TableCell({
    borders,
    width: { size: widthDxa, type: WidthType.DXA },
    shading: { fill: COLORS.headerBg, type: ShadingType.CLEAR },
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [new TextRun({ text, bold: true, color: COLORS.headerText, size: 18, font: "Arial" })],
    })],
  });
}

function dataCell(text, widthDxa, opts = {}) {
  const { bg = COLORS.rowWhite, color = "000000", bold = false, align = AlignmentType.LEFT } = opts;
  return new TableCell({
    borders,
    width: { size: widthDxa, type: WidthType.DXA },
    shading: { fill: bg, type: ShadingType.CLEAR },
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({
      alignment: align,
      children: [new TextRun({ text: String(text), color, bold, size: 18, font: "Arial" })],
    })],
  });
}

function policyTable(policies, hitsColumnLabel) {
  const cols = [500, 2200, 1300, 1400, 3960];
  const headerRow = new TableRow({
    tableHeader: true,
    children: [
      headerCell("#", cols[0]),
      headerCell("Policy Name", cols[1]),
      headerCell(hitsColumnLabel, cols[2]),
      headerCell("Status", cols[3]),
      headerCell("Recommendation", cols[4]),
    ],
  });

  const dataRows = policies.map((p, i) => {
    const { text: stColor, bg: stBg } = statusColor(p.status);
    const rowBg = i % 2 === 0 ? COLORS.rowWhite : COLORS.rowAlt;
    return new TableRow({
      children: [
        dataCell(p.order, cols[0], { bg: rowBg, align: AlignmentType.CENTER }),
        dataCell(p.name, cols[1], { bg: rowBg }),
        dataCell(p.hits.toLocaleString(), cols[2], { bg: rowBg, align: AlignmentType.CENTER }),
        dataCell(statusLabel(p.status), cols[3], { bg: stBg, color: stColor, bold: true }),
        dataCell(p.recommendation, cols[4], { bg: rowBg }),
      ],
    });
  });

  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: cols,
    rows: [headerRow, ...dataRows],
  });
}

function heading(text, level = HeadingLevel.HEADING_1) {
  return new Paragraph({ heading: level, children: [new TextRun({ text, font: "Arial" })] });
}

function body(text, spacing = 160) {
  return new Paragraph({
    spacing: { after: spacing },
    children: [new TextRun({ text, size: 20, font: "Arial" })],
  });
}

function space(after = 200) {
  return new Paragraph({ spacing: { after }, children: [] });
}

async function main() {
  const args = parseArgs();
  const input = loadInput(args.input);

  const reportDate = input.reportDate || new Date().toISOString().slice(0, 10);
  const purposeText = input.purposeText || "This report analyses mail policy hit counts on the Cisco ESA to identify policies with no or minimal traffic.";

  // Build time range display
  let timeRangeDisplay = "Custom";
  if (input.analysis_time_range && input.analysis_time_range.start && input.analysis_time_range.end) {
    timeRangeDisplay = `${input.analysis_time_range.start} to ${input.analysis_time_range.end}`;
  }

  const incomingPolicies = normalizePolicyList(input.incomingPolicies);
  const outgoingPolicies = normalizePolicyList(input.outgoingPolicies);
  const hasOutgoing = outgoingPolicies.length > 0;

  const doc = new Document({
    styles: {
      default: { document: { run: { font: "Arial", size: 20 } } },
    },
    sections: [{
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 },
        },
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "2E5F9E", space: 1 } },
            children: [
              new TextRun({ text: "ESA Policy Health Check Report", bold: true, font: "Arial", size: 20, color: "1F3864" }),
              new TextRun({ text: `\t${reportDate}`, font: "Arial", size: 20, color: "888888" }),
            ],
            tabStops: [{ type: "right", position: 9360 }],
          })],
        }),
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            border: { top: { style: BorderStyle.SINGLE, size: 6, color: "2E5F9E", space: 1 } },
            alignment: AlignmentType.CENTER,
            children: [
              new TextRun({ text: "Cisco ESA - Policy Fine-Tuning Report  |  Page ", font: "Arial", size: 16, color: "888888" }),
              new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 16, color: "888888" }),
            ],
          })],
        }),
      },
      children: [
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 480, after: 120 },
          children: [new TextRun({ text: "ESA Policy Health Check", bold: true, size: 52, font: "Arial", color: COLORS.headerBg })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { after: 80 },
          children: [new TextRun({ text: "Policy Fine-Tuning and Housekeeping Report", size: 28, font: "Arial", color: "2E5F9E" })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { after: 480 },
          children: [new TextRun({ text: `Report Date: ${reportDate}   |   Query Period: ${timeRangeDisplay}`, size: 20, font: "Arial", color: "888888" })],
        }),

        heading("Purpose", HeadingLevel.HEADING_1),
        body(purposeText),
        space(),

        heading("Query Parameters", HeadingLevel.HEADING_1),
        body(`Time Range: ${timeRangeDisplay}`),
        body(`Days Queried: ${input.queryParameters?.days_to_query || "N/A"}`),
        body(`Top N Policies Requested: ${input.queryParameters?.top_n_policies || "N/A"}`),
        space(),

        heading(hasOutgoing ? "Incoming Mail Policies" : "Configured Policies (Combined)", HeadingLevel.HEADING_1),
        body("The table below shows policy hit counts, status, and recommended action."),
        space(120),
        policyTable(incomingPolicies, "Hit Count"),
        space(),

        ...(hasOutgoing
          ? [
              heading("Outgoing Mail Policies", HeadingLevel.HEADING_1),
              body("The table below shows policy hit counts, status, and recommended action."),
              space(120),
              policyTable(outgoingPolicies, "Hit Count"),
              space(),
            ]
          : []),
      ],
    }],
  });

  const buf = await Packer.toBuffer(doc);
  fs.writeFileSync(args.output, buf);
  console.log(`Generated ${args.output}`);
}

main().catch((err) => {
  console.error("Failed to generate report:", err.message);
  process.exit(1);
});
