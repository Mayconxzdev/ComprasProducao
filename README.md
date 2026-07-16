# ComprasVesper — cockpit desktop de cotações

Aplicativo desktop em **Python + PySide6** para organizar a etapa inicial de compras: encontrar fornecedores, montar pedidos de cotação, enviar ordens de compra e acompanhar respostas sem transformar uma planilha em um processo manual.

> Esta publicação é uma versão demonstrativa e reproduzível da aplicação 4.8.0. Catálogos, empresas, endereços, caixas de e-mail, assinaturas e caminhos corporativos foram substituídos por dados fictícios. No modo demo, envio SMTP, leitura IMAP e sincronização de rede ficam bloqueados.

## O problema que resolvi

Em operações de compras, dados de fornecedores normalmente vivem em planilhas, enquanto o pedido de cotação, os anexos e o acompanhamento da resposta ficam espalhados entre e-mails e mensagens. O resultado é demora para localizar contatos, pouca rastreabilidade e dificuldade de saber quem respondeu.

O ComprasVesper coloca esse fluxo em uma única interface desktop:

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

## Interface em execução

As imagens abaixo são capturas da interface PySide6 real, geradas pela própria aplicação em modo demonstração com a base anônima incluída neste repositório.

### Escolha do fluxo

![Tela inicial com os quatro tipos de solicitação](docs/assets/ui-dashboard-real.png)

### Cotação de frete

![Composer de frete com dados, anexos e transportadoras](docs/assets/ui-freight-real.png)

### Acompanhamento de respostas

![Cockpit Acompanhar com painel de resposta e dados comerciais](docs/assets/ui-tracking-real.png)

## Fluxos de produto

| Fluxo | O que acontece |
| --- | --- |
| **Nova cotação** | A pessoa escolhe material, painéis EX, frete ou ordem de compra e começa com campos específicos para a tarefa. |
| **Busca de destinatários** | A busca tolera pequenos erros de digitação e usa empresa, contato, e-mail e produto para encontrar opções relevantes. |
| **Frete** | Transportadoras padrão e fornecedores encontrados na base podem ser combinados, com seleção visual leve baseada em delegate Qt. |
| **Acompanhar** | Respostas IMAP são correlacionadas com a referência da cotação; a tela mostra resposta, dados comerciais extraídos e pendências. |
| **Auditoria** | Envios e ações ficam disponíveis para histórico e exportação XLSX. |

## Arquitetura

| Camada | Papel |
| --- | --- |
| [`app/qt`](app/qt) | Shell desktop, navegação, tema, componentes e páginas operacionais. |
| [`app/application`](app/application) | Contexto, casos de uso, jobs e inicialização. |
| [`app/catalog`](app/catalog) | Índice, normalização e busca de fornecedores. |
| [`app/core`](app/core) | E-mail, IMAP, tracking, histórico, arquivos, configuração e regras de negócio. |
| [`tests`](tests) | Contratos de interface, busca, tracking e modo demonstração. |
| [`installer`](installer) | Empacotamento Windows via Inno Setup. |

Documentos técnicos: [arquitetura](docs/architecture.md), [segurança](docs/security.md) e [validação](docs/testing.md).

## Executar a demonstração local

Pré-requisitos: Windows e Python 3.12+ (ou `uv`).

```powershell
uv venv .venv --python 3.12
uv pip install -r requirements-dev.txt --python .venv\Scripts\python.exe

$env:COMPRAS_VESPER_DEMO = "1"
$env:APPDATA = "$PWD\.demo-runtime"
.\.venv\Scripts\python.exe -m app.main
```

O catálogo aberto pela demonstração é [`examples/fornecedores-demo.xlsx`](examples/fornecedores-demo.xlsx). Ele usa o domínio `.invalid`, reservado para exemplos e incapaz de encaminhar e-mails reais.

## Qualidade e evidências

```powershell
$env:COMPRAS_VESPER_DEMO = "1"
$env:APPDATA = "$PWD\.demo-runtime"
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m compileall -q app
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m app.tools.smoke_core
```

Na revisão desta versão pública foram executados **46 testes**, além da compilação dos módulos e do smoke do núcleo. O GitHub Actions replica a checagem em Windows.

## Limites deliberados desta versão pública

- Não há credenciais, dados de clientes, fornecedores reais ou caminhos de NAS no repositório.
- O modo de demonstração bloqueia o envio SMTP, a leitura IMAP e a sincronização de rede por código, não apenas por configuração visual.
- Para implantação real, a organização deve configurar suas próprias contas, diretório de dados, políticas de backup e controle de acesso.

## Autor

Desenvolvido por [Maycon Ferreira](https://github.com/Mayconxzdev) como um case de automação de processos, produto desktop e integração de sistemas para compras.
