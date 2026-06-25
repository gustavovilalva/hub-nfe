# Hub NFe — Central de XMLs de Notas Fiscais

Central local para sincronizar, armazenar e pesquisar XMLs de NFe do **Tiny ERP**.  
Os XMLs são salvos em disco organizados por ano/mês, e os metadados ficam indexados em um banco SQLite para pesquisa rápida.

---

## Estrutura do projeto

```
hub_nfe/
├── main.py          ← CLI principal (sincronizar, buscar, status...)
├── tiny_api.py      ← Conector com a API do Tiny ERP
├── database.py      ← Banco SQLite + organização de arquivos
├── xml_parser.py    ← Parser do XML da NFe
├── requirements.txt
├── .env.example     ← Modelo de configuração
└── xmls/            ← XMLs salvos (criado automaticamente)
    └── 2024/
        └── 01/
            └── 35240100000000000000550010000000001000000000.xml
```

---

## Instalação

### 1. Requisitos

- Python 3.9 ou superior

### 2. Instalar dependências

```bash
cd hub_nfe
pip install -r requirements.txt
```

### 3. Configurar o token do Tiny

1. Acesse o Tiny ERP → **Minha Conta → Integrações → API**
2. Gere ou copie seu token de acesso
3. Copie o arquivo de exemplo e preencha:

```bash
cp .env.example .env
```

Edite o `.env`:
```
TINY_TOKEN=cole_seu_token_aqui
XML_DIR=./xmls
DB_PATH=./hub_nfe.db
```

---

## Uso

### Sincronizar todas as NFes autorizadas

```bash
python main.py sincronizar
```

### Sincronizar por período

```bash
python main.py sincronizar --data-inicio 01/01/2024 --data-fim 31/12/2024
```

### Sincronizar notas canceladas também

```bash
python main.py sincronizar --situacao ""
```

### Ver status do hub

```bash
python main.py status
```

Exemplo de saída:
```
──────────────── Status do Hub NFe ────────────────
  Banco de dados : ./hub_nfe.db
  Diretório XMLs : ./xmls

  Total de notas   : 1.243
  Valor acumulado  : R$ 4.521.890,00
  Nota mais antiga : 2022-03-01
  Nota mais recente: 2024-12-31
  Meses cobertos   : 34
```

---

## Pesquisar notas

### Por número

```bash
python main.py buscar --numero 1234
```

### Por nome do destinatário

```bash
python main.py buscar --nome "Empresa ABC"
```

### Por CNPJ

```bash
python main.py buscar --cnpj 12345678000100
```

### Por período

```bash
python main.py buscar --data-inicio 2024-01-01 --data-fim 2024-03-31
```

### Por faixa de valor

```bash
python main.py buscar --valor-min 1000 --valor-max 50000
```

### Por UF

```bash
python main.py buscar --uf SP
```

### Combinando filtros + mostrar caminho do XML

```bash
python main.py buscar --nome "Cliente X" --data-inicio 2024-01-01 --xml
```

### Obter XML de uma nota específica

```bash
# Pelo número da nota
python main.py obter-xml 1234

# Pela chave de acesso completa
python main.py obter-xml 35240100000000000000550010000000001000000000
```

---

## Agendamento automático (sincronização diária)

### Linux / macOS — via cron

Abra o crontab:
```bash
crontab -e
```

Adicione uma linha para rodar todo dia às 02:00:
```
0 2 * * * cd /caminho/para/hub_nfe && python main.py sincronizar >> sync.log 2>&1
```

### Windows — via Agendador de Tarefas

1. Abra o **Agendador de Tarefas** → Criar Tarefa Básica
2. Defina o gatilho: **Diariamente** às 02:00
3. Ação: **Iniciar um programa**
   - Programa: `python`
   - Argumentos: `main.py sincronizar`
   - Iniciar em: `C:\caminho\para\hub_nfe`

---

## Estrutura do banco de dados

Tabela `notas_fiscais`:

| Campo           | Descrição                              |
|-----------------|----------------------------------------|
| chave_acesso    | Chave de 44 dígitos (índice único)     |
| numero          | Número da NF                           |
| serie           | Série                                  |
| data_emissao    | Data YYYY-MM-DD                        |
| tipo            | `saida` ou `entrada`                   |
| situacao        | Autorizada / Cancelada / etc.          |
| valor_total     | Valor em R$                            |
| cnpj_emitente   | CNPJ do emitente                       |
| nome_emitente   | Razão social do emitente               |
| cnpj_dest       | CNPJ/CPF do destinatário               |
| nome_dest       | Razão social do destinatário           |
| municipio_dest  | Município do destinatário              |
| uf_dest         | UF do destinatário                     |
| natureza_op     | Natureza da operação                   |
| xml_path        | Caminho do arquivo XML no disco        |
| sincronizado_em | Data/hora da última sincronização      |

---

## Dicas

- Os XMLs ficam em `xmls/ANO/MES/chave_acesso.xml` — fácil de fazer backup.
- O banco SQLite (`hub_nfe.db`) é um arquivo único, portátil e pode ser aberto com qualquer cliente SQLite (ex: [DB Browser for SQLite](https://sqlitebrowser.org/)).
- Para exportar uma lista de notas para Excel, você pode consultar o banco diretamente ou estender o `buscar` para gerar CSV.
