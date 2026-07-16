# Arquitetura da aplicação

O ComprasVesper é um aplicativo Windows local-first. A interface foi projetada para que uma pessoa de compras consiga abrir uma nova solicitação sem navegar por telas administrativas.

## Componentes

```text
PySide6 / shell operacional
  ├── Nova cotação: Material, Painéis EX, Frete e OC
  ├── Fornecedores: pesquisa e seleção de destinatários
  ├── Acompanhar: histórico, resposta e próxima ação
  └── Configurações: SMTP, IMAP, assinaturas e tipos personalizados

Casos de uso e jobs
  ├── pré-aquecimento da base de fornecedores
  ├── reindexação e cache local
  ├── fila de e-mail
  └── sincronização de respostas

Núcleo
  ├── XLSX / catálogo / busca tolerante a erros
  ├── templates e composição de e-mail
  ├── SMTP e rastreamento CV-*
  ├── IMAP e correlação de respostas
  └── histórico, anexos e exportação
```

## Decisões relevantes

- **Planilha como fonte operacional, não como interface:** a organização mantém um formato familiar, enquanto o app fornece pesquisa, validação, seleção e contexto.
- **Envio revisável:** destinatários, texto e anexos permanecem visíveis antes do envio.
- **Correlação por referência:** cada envio pode carregar uma referência interna que permite localizar respostas e apresentar o estado da cotação sem abrir o cliente de e-mail.
- **UI performática para frete:** o fluxo usa itens nativos e delegate de pintura para não criar widgets em lote ao carregar transportadoras.
- **Operação degradada:** cache local e filas permitem que a aplicação informe o estado de dependências em vez de travar a tela.
