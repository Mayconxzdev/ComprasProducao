import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputPath = fileURLToPath(new URL("../examples/fornecedores-demo.xlsx", import.meta.url));
const previewPath = fileURLToPath(new URL("../docs/assets/fornecedores-demo-preview.png", import.meta.url));
const workbook = Workbook.create();
const intro = workbook.worksheets.add("LEIA-ME");
const suppliers = workbook.worksheets.add("Fornecedores");
intro.showGridLines = false;
suppliers.showGridLines = false;

intro.getRange("A1:H1").merge();
intro.getRange("A1").values = [["Base demonstrativa de fornecedores"]];
intro.getRange("A2:H2").merge();
intro.getRange("A2").values = [["Dados inteiramente fictícios para abrir, testar e apresentar o ComprasVesper sem rede corporativa."]];
intro.getRange("A4:H5").merge();
intro.getRange("A4").values = [["A aba Fornecedores é a fonte usada pelo aplicativo. EMPRESA, MATERIAL / PRODUTO e EMAIL são campos obrigatórios; os demais enriquecem a busca e o contexto do pedido."]];
intro.getRange("A1:H1").format = { fill: "#111827", font: { bold: true, color: "#FFFFFF", size: 16 }, verticalAlignment: "center" };
intro.getRange("A2:H2").format = { fill: "#E8EEF8", font: { color: "#334155", italic: true }, wrapText: true, verticalAlignment: "center" };
intro.getRange("A4:H5").format = { fill: "#F8FAFC", font: { color: "#334155" }, wrapText: true, verticalAlignment: "center" };
intro.getRange("A1:H1").format.columnWidth = 18;
intro.getRange("1:1").format.rowHeight = 30;
intro.getRange("2:2").format.rowHeight = 32;
intro.getRange("4:5").format.rowHeight = 25;

const headers = [["EMPRESA", "MATERIAL / PRODUTO", "EMAIL", "TELEFONE", "NOME DO CONTATO", "ENDEREÇO", "BAIRRO / CIDADE", "STATUS"]];
const rows = [
  ["Metalúrgica Horizonte", "Chapa galvanizada", "vendas@metalurgica-horizonte.invalid", "+55 (00) 3000-1001", "Marina Rocha", "Rua das Indústrias, 101", "Distrito Industrial / Cidade", "Ativo"],
  ["Aço & Corte Modelo", "Chapa galvanizada", "comercial@aco-corte-modelo.invalid", "+55 (00) 3000-1002", "Rafael Lima", "Av. Central, 202", "Centro / Cidade", "Ativo"],
  ["Soluções em Fixação", "Parafuso inox", "cotacao@solucoes-fixacao.invalid", "+55 (00) 3000-1003", "Camila Alves", "Rua do Comércio, 303", "Centro / Cidade", "Ativo"],
  ["TechMec Componentes", "Parafuso inox", "vendas@techmec-componentes.invalid", "+55 (00) 3000-1004", "Diego Santos", "Rod. Modelo, km 4", "Polo Fabril / Cidade", "Ativo"],
  ["EletroControle", "Inversor de frequência", "propostas@eletrocontrole.invalid", "+55 (00) 3000-1005", "Ana Martins", "Rua da Tecnologia, 505", "Centro / Cidade", "Ativo"],
  ["Automação Industrial Alfa", "Inversor de frequência", "atendimento@automacao-alfa.invalid", "+55 (00) 3000-1006", "Bruno Costa", "Av. das Máquinas, 606", "Polo Fabril / Cidade", "Ativo"],
  ["Fluxo Ventilação", "Ventilador axial", "comercial@fluxo-ventilacao.invalid", "+55 (00) 3000-1007", "Paula Nunes", "Rua do Ar, 707", "Distrito Industrial / Cidade", "Ativo"],
  ["Logística Ponto Sul", "Frete dedicado", "cotacao@logistica-ponto-sul.invalid", "+55 (00) 3000-1008", "Lucas Ferreira", "Av. das Rotas, 808", "Centro / Cidade", "Ativo"],
];
suppliers.getRange("A1:H1").values = headers;
suppliers.getRange(`A2:H${rows.length + 1}`).values = rows;
suppliers.tables.add(`A1:H${rows.length + 1}`, true, "FornecedoresDemo");
suppliers.getRange("A1:H1").format = { fill: "#0F4C81", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
suppliers.getRange(`A1:H${rows.length + 1}`).format.borders = { preset: "inside", style: "thin", color: "#D7DEE8" };
suppliers.getRange(`A1:H${rows.length + 1}`).format.borders = { preset: "outside", style: "thin", color: "#9AA7B8" };
suppliers.getRange(`C2:C${rows.length + 1}`).format.font = { color: "#0F4C81" };
suppliers.getRange(`H2:H${rows.length + 1}`).conditionalFormats.add("containsText", { text: "Ativo", format: { fill: "#DCFCE7", font: { color: "#166534", bold: true } } });
for (const [column, width] of [["A:A",28],["B:B",26],["C:C",38],["D:D",18],["E:E",20],["F:F",25],["G:G",28],["H:H",12]]) suppliers.getRange(column).format.columnWidth = width;
suppliers.getRange("1:1").format.rowHeight = 28;
suppliers.freezePanes.freezeRows(1);

await fs.mkdir(fileURLToPath(new URL("../examples/", import.meta.url)), { recursive: true });
await fs.mkdir(fileURLToPath(new URL("../docs/assets/", import.meta.url)), { recursive: true });
const check = await workbook.inspect({ kind: "region", sheetId: "Fornecedores", range: "A1:H10", maxChars: 3000 });
if (!String(check.ndjson || check).includes("Metalúrgica Horizonte")) throw new Error("Falha na verificação da base demonstrativa.");
const preview = await workbook.render({ sheetName: "LEIA-ME", autoCrop: "all", scale: 1, format: "png" });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(`Workbook written to ${outputPath}`);
