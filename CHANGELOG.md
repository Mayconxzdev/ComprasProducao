# Changelog

## 4.8.0 — versão pública demonstrativa

- Reestruturada a entrada do produto em quatro fluxos: cotação de material,
  painéis EX, frete e ordem de compra.
- Unificada a busca de destinatários por empresa, contato, e-mail e produto,
  incluindo tolerância a pequenas variações de digitação.
- Refinado o fluxo de frete com lista nativa/delegate Qt para manter a seleção
  visual leve mesmo com muitas transportadoras.
- Consolidado o cockpit **Acompanhar**, com rastreamento por referência `CV-*`,
  correlação IMAP, resposta limpa, dados comerciais e próxima ação.
- Incluídos modo demonstração bloqueado por código, planilha anônima,
  screenshots reais da UI, documentação de arquitetura/segurança e validação
  automatizada no GitHub Actions.

### Verificação desta publicação

- 46 testes automatizados;
- compilação dos módulos Python;
- auditoria de layout e auditoria estática;
- smoke do núcleo em modo demonstração;
- varredura de identificadores corporativos conhecidos antes da publicação.

> O histórico operacional, credenciais e informações de implantação da versão
> privada não fazem parte deste repositório público.
