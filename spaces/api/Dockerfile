# Open GovTech Index: the site, REST API and MCP endpoint, as a free CPU Docker Space.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

# Installs the package straight from GitHub. Set a GOVTECH_REF Space variable to build a branch,
# tag or commit instead of main; Hugging Face passes Space variables to Docker as build args.
ARG GOVTECH_REF=main
RUN pip install --no-cache-dir --user \
    "govtech-scraper[api] @ git+https://github.com/AndreasThinks/open-govtech-report@${GOVTECH_REF}"

COPY --chown=user start.sh .
EXPOSE 7860
CMD ["sh", "start.sh"]
