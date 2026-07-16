# Segurança e dados demonstrativos

Esta é uma cópia pública do projeto. Nenhuma configuração de empresa foi reutilizada.

## O que foi removido ou neutralizado

- endereços, CNPJ, fornecedores, contatos, e-mails e assinaturas;
- destinos de rede, atualizações internas e perfis Thunderbird;
- contas SMTP/IMAP e qualquer senha, token ou chave;
- dados de execução, logs, histórico, bancos locais e artefatos de build.

## Garantias do modo demo

Com `COMPRAS_VESPER_DEMO=1`, a aplicação carrega somente `examples/fornecedores-demo.xlsx`, ignora configuração de rede, desativa IMAP e retorna falha controlada para qualquer tentativa de SMTP. O mecanismo é coberto pelos testes.

## Uso fora da demonstração

Configure credenciais, diretórios e políticas da sua própria organização. Não versione `config.json`, bancos locais, exportações, anexos nem arquivos `.env`.

## Credenciais em implantação real

O compartilhamento de configuração replica somente metadados de SMTP/IMAP e
fontes de dados. Senhas e chaves de API não são exportadas, importadas ou
sincronizadas: cada estação precisa registrá-las localmente com DPAPI. Base64
é tratado apenas como formato legado e nunca como mecanismo de proteção.
