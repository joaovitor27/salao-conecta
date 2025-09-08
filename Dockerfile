# Usa a imagem oficial do Python, versão 3.11, com a base slim para um tamanho menor
FROM python:3.11-slim

# Define o diretório de trabalho dentro do container
WORKDIR /app

# Instala as dependências de sistema necessárias para o banco de dados
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev

# Copia o gerenciador de pacotes uv e o arquivo de dependências para o container
COPY . /app

# Instala as dependências usando o uv
RUN pip install uv
RUN uv sync

# Copia o código da sua aplicação para o container
COPY . /app

# Define o comando padrão para rodar a aplicação
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]