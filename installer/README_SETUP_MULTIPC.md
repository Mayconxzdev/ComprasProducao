# Instalação em rede — guia público

Este repositório contém um modo de demonstração seguro e um exemplo de base
local. Para uma implantação real, a organização deve configurar suas próprias
fontes de fornecedores, SMTP/IMAP, políticas de credenciais e local de cache.

## Sequência recomendada

1. Instale o aplicativo em uma estação piloto.
2. Cadastre os caminhos compartilhados e valide a leitura da planilha.
3. Configure SMTP/IMAP usando as credenciais da própria organização.
4. Faça um envio de teste para uma caixa interna.
5. Somente depois habilite a sincronização da configuração para as demais
   estações.

Credenciais não fazem parte do instalador, do repositório ou do arquivo de
configuração compartilhado. Consulte `docs/security.md` antes da implantação.
