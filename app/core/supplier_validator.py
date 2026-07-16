import json
import urllib.request
import urllib.parse
from dataclasses import dataclass
from typing import Optional

@dataclass
class CompanyData:
    cnpj: str
    razao_social: str
    nome_fantasia: str
    situacao: str # ATIVA, BAIXADA, ETC
    data_inicio_atividade: str
    endereco_completo: str
    bairro: str
    cidade: str
    uf: str
    cep: str
    telefone: str
    email: str
    map_link: str # Google Maps Link

class SupplierValidator:
    """
    Validador de Fornecedores 'Anti-Golpe' 🏢
    Consulta Receita Federal (via BrasilAPI) e gera link do Street View.
    """

    BASE_URL = "https://brasilapi.com.br/api/cnpj/v1/"

    def validate_cnpj(self, cnpj: str) -> Optional[CompanyData]:
        """
        Consulta dados do CNPJ e retorna objeto CompanyData.
        Retorna None se falhar ou não encontrar.
        """
        clean_cnpj = ''.join(filter(str.isdigit, cnpj))

        if len(clean_cnpj) != 14:
            print(f"CNPJ inválido: {cnpj}")
            return None

        try:
            url = f"{self.BASE_URL}{clean_cnpj}"
            req = urllib.request.Request(url, headers={'User-Agent': 'ComprasApp/1.0'})

            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status != 200:
                    return None

                data = json.loads(response.read().decode('utf-8'))

                # Montar endereço para o Maps
                logradouro = data.get('logradouro', '')
                numero = data.get('numero', '')
                bairro = data.get('bairro', '')
                municipio = data.get('municipio', '')
                uf = data.get('uf', '')
                cep = data.get('cep', '')

                endereco_str = f"{logradouro}, {numero} - {bairro}, {municipio} - {uf}, {cep}"
                encoded_addr = urllib.parse.quote(endereco_str)

                # Link direto pro Street View (ou busca de mapas)
                # O mais seguro é a busca geral, que mostra o pin e a foto lateral
                map_link = f"https://www.google.com/maps/search/?api=1&query={encoded_addr}"

                return CompanyData(
                    cnpj=clean_cnpj,
                    razao_social=data.get('razao_social', ''),
                    nome_fantasia=data.get('nome_fantasia', '') or data.get('razao_social', ''),
                    situacao=data.get('descricao_situacao_cadastral', 'DESCONHECIDA'),
                    data_inicio_atividade=data.get('data_inicio_atividade', ''),
                    endereco_completo=endereco_str,
                    bairro=bairro,
                    cidade=municipio,
                    uf=uf,
                    cep=cep,
                    telefone=data.get('ddd_telefone_1', ''),
                    email=data.get('email', ''),
                    map_link=map_link
                )

        except Exception as e:
            print(f"Erro validando CNPJ {cnpj}: {e}")
            return None

# Teste
if __name__ == "__main__":
    validator = SupplierValidator()
    # Exemplo: Google Brasil
    res = validator.validate_cnpj("06990590000123")
    if res:
        print(f"Empresa: {res.nome_fantasia}")
        print(f"Situação: {res.situacao}")
        print(f"Endereço: {res.endereco_completo}")
        print(f"Maps: {res.map_link}")
    else:
        print("Erro ou não encontrado.")
