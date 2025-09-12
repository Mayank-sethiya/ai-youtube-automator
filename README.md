# AI YouTube and Instagram Automator

This project contains the "AI Mind" for a fully autonomous content creation and publishing agent. It uses an AI agent framework to decide on video topics, generate scripts, and then delegates the execution (video generation and publishing) to a separate n8n workflow.

## Core Components

- **AI Mind (`/agent`)**: Python code responsible for reasoning, planning, and decision-making.
- **n8n Workflows ("Hands")**: The execution layer that interacts with external APIs for video/audio generation and publishing to social platforms.

## Setup

1.  Clone this repository.
2.  Install the required Python packages: `pip install -r requirements.txt`
3.  Configure environment variables in a `.env` file for API keys.
4.  Set up the corresponding n8n workflows and configure the webhook URLs.
