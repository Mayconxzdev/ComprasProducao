<div align="center">

# Compras e Cotações — pedidos, fornecedores e respostas

**Desenvolvi uma aplicação desktop para reunir pedidos de compra, cotações de fornecedores e acompanhamento das respostas em um só lugar.**

[![Validação](https://github.com/Mayconxzdev/ComprasProducao/actions/workflows/validate.yml/badge.svg)](https://github.com/Mayconxzdev/ComprasProducao/actions/workflows/validate.yml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![PySide6](https://img.shields.io/badge/Desktop-PySide6-41CD52?logo=qt&logoColor=white)
![SQLite](https://img.shields.io/badge/Queue-SQLite%20WAL-003B57?logo=sqlite&logoColor=white)

[Case no portfólio](https://mayconxzdev.github.io/cases/compras-e-cotacoes/) · [Arquitetura](docs/architecture.md) · [Segurança](docs/security.md) · [Testes](docs/testing.md)


</div>

A solução organiza uma rotina que antes dependia de planilhas e e-mails separados: localizar fornecedores, preparar pedidos de cotação, enviar ordens de compra e acompanhar respostas.

> Esta é uma edição demonstrativa e reproduzível. Empresas, fornecedores, contatos, caixas de e-mail, assinaturas e caminhos foram substituídos por dados fictícios. No modo demonstração, envio de e-mail, leitura de respostas e sincronização de rede ficam bloqueados.

## Problema que resolvi

Os dados de fornecedores ficavam em planilhas, enquanto pedidos de cotação, anexos e acompanhamento de respostas ficavam distribuídos entre e-mails e mensagens. Isso aumentava o tempo para localizar contatos e dificultava saber quem havia respondido.

O aplicativo reúne esse fluxo em uma única interface:

```text
Base XLSX de fornecedores
        ↓
Busca por produto, empresa, contato ou e-mail
        ↓
Material · Painéis EX · Frete · Ordem de compra
        ↓
Revisão humana de destinatários, texto e anexos
        ↓
SMTP configurado pela organização
        ↓
Histórico + acompanhamento IMAP de respostas + próxima ação
```

## Uso atual

A versão interna é utilizada pela equipe sempre que surge a necessidade de cotar com fornecedores. A edição pública mantém a interface, a arquitetura e as principais regras, mas usa fornecedores e dados fictícios.

## Interface

As telas abaixo mostram a aplicação com dados demonstrativos: uma visão inicial, a preparação de uma cotação de frete e o acompanhamento de respostas.

### Pedidos de compra

![Tela inicial do aplicativo Compras com opções para cotações e ordens de compra](docs/assets/ui-dashboard-real.png)

### Cotação de frete

![Formulário demonstrativo para reunir carga, anexos e destinatários da cotação](docs/assets/ui-freight-real.png)

### Acompanhamento de respostas

![Tela demonstrativa para consultar respostas recebidas e pendências de uma cotação](docs/assets/ui-tracking-real.png)

## Fluxos principais

| Fluxo | O que acontece |
| --- | --- |
| **Nova cotação** | A pessoa escolhe material, painéis EX, frete ou ordem de compra e recebe campos específicos para a tarefa. |
| **Busca de destinatários** | A pesquisa tolera pequenos erros de digitação e usa empresa, contato, e-mail e produto para encontrar opções. |
| **Frete** | Transportadoras padrão e fornecedores da base podem ser combinados em uma seleção visual. |
| **Acompanhar** | Respostas IMAP são correlacionadas com a referência da cotação; a tela mostra resposta, dados comerciais e pendências. |
| **Histórico** | Envios e ações ficam disponíveis para consulta e exportação XLSX. |

## Decisões técnicas

- **Revisão humana antes do envio:** a aplicação mostra e valida destinatários, corpo e anexos antes de qualquer saída.
- **Fila local durável:** falhas de SMTP entram em SQLite com WAL, recuperação de itens presos, backoff progressivo e chave de idempotência persistida.
- **Reenvio tratado como best effort:** SMTP não oferece confirmação transacional ponta a ponta; por isso, o sistema mantém a auditoria visível.
- **Segredos locais:** configuração compartilhada contém apenas metadados; senhas e chaves ficam protegidas por DPAPI em cada estação.
- **Planilha como entrada, não como interface principal:** XLSX continua familiar, enquanto índice local, busca e validações retiram o trabalho manual da planilha.

## Arquitetura

| Camada | Papel |
| --- | --- |
| [`app/qt`](app/qt) | Shell desktop, navegação, tema, componentes e páginas operacionais. |
| [`app/application`](app/application) | Contexto, casos de uso, jobs e inicialização. |
| [`app/catalog`](app/catalog) | Índice, normalização e busca de fornecedores. |
| [`app/core`](app/core) | E-mail, IMAP, tracking, histórico, arquivos, configuração e regras de negócio. |
| [`tests`](tests) | Contratos de interface, busca, tracking e modo demonstração. |
| [`installer`](installer) | Empacotamento Windows via Inno Setup. |

Documentos técnicos: [arquitetura](docs/architecture.md), [segurança](docs/security.md) e [testes](docs/testing.md).

## Executar a demonstração

Pré-requisitos: Windows e Python 3.12+ ou `uv`.

```powershell
uv venv .venv --python 3.12
uv pip install -r requirements-dev.txt --python .venv\Scripts\python.exe

$env:COMPRAS_DEMO = "1"
$env:APPDATA = "$PWD\.demo-runtime"
.\.venv\Scripts\python.exe -m app.main
```

O catálogo usado pela demonstração é [`examples/fornecedores-demo.xlsx`](examples/fornecedores-demo.xlsx). Ele utiliza o domínio `.invalid`, reservado para exemplos e incapaz de encaminhar e-mails reais.

## Testes

```powershell
$env:COMPRAS_DEMO = "1"
$env:APPDATA = "$PWD\.demo-runtime"
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m compileall -q app
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m app.tools.smoke_core
```

A suíte cobre contratos da interface, busca de fornecedores, acompanhamento de respostas e os limites do modo demonstração. O GitHub Actions executa essas verificações em Windows.

## Estado e limites

- não há credenciais, dados de clientes, fornecedores reais ou caminhos de NAS no repositório;
- o modo demonstração bloqueia SMTP, IMAP e sincronização de rede por código;
- uma implantação real precisa configurar contas, diretório de dados, backup e controle de acesso próprios.

## Autor

**Maycon Ferreira** — produto, automação de processos, interface desktop e integração de sistemas para compras.
