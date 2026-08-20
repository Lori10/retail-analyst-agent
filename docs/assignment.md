# AI Technical Assignment – Retail Company

## The Challenge: Design and Build a Data Analysis Chat Assistant

You are building an internal data analysis agent for a Retail Company's non-technical executive team (Store and Regional Managers). These managers need to ask complex questions about sales, inventory, and performance (e.g., *"Why are the users for state X underspending? And how does it compare to users in state Y?"*, *"Why did our churn rate spike last month?"*, *"Create a report for Q1 including insights and action items for Q2"*).

The agent will have access to:

- **The Database:** A read-only connection to a SQL database containing raw transaction logs.
- **The "Golden Knowledge" Bucket:** A data lake containing historical "Trios" (Question → SQL Query → Analyst Report) created by human experts (Theoretical).

### The Task

Design a production-ready full High-Level Design (HLD), accompanied by a detailed technical explanation, and build a data analysis chat-agent prototype that allows executives to ask questions about this data naturally and discuss it. The system should be easily extendable for new capabilities (generating graphs, sending reports via mail, searching the web for trends, etc.) and new data sources.

> **Note:** We expect to see what services you aim to use, what the communication is between the components, how and where data is stored and handled. The "detailed technical explanation" needs to be detailed enough for us to understand how this system will function in production.

---

## Requirements

1. **Hybrid Intelligence:** The agent cannot rely on BQ data alone. It must use the "Golden Bucket" to understand how analysts previously interpreted questions and apply similar logic to new queries. Explain how you will update the golden bucket over time and how you will provide relevant data at user query time.

2. **Safety & PII Masking:** The agent is only allowed to answer analysis questions and needs to be safeguarded against malicious users. The raw transaction logs contain sensitive data. The agent is strictly forbidden from displaying these PIIs in the final output.

3. **High-Stakes Oversight (Destructive Ops):** While the DB is read-only, the agent manages a "Saved Reports" library. The agent must support inputs like *"Delete all reports mentioning Client X"* / *"Delete all the reports we made in this conversation"*. This is a destructive action and requires a strict confirmation flow before execution, without breaking UX – the users are allowed to delete their own reports.

4. **Continuous Improvement (The Learning Loop):**
   1. **User Level:** The agent should remember that "Manager A" prefers tables while "Manager B" prefers bullet points, as well as learn user preferences over time, for example: how deep they like the analysis, do they prefer charts or text, etc.
   2. **System Level:** The agent should be able to learn from past interactions and improve itself.

5. **Resilience & Graceful Error Handling:** The system must detect syntax errors or empty returns and attempt to self-correct before giving up, without crashing the user interface and without inflating costs. The system should be resilient to API/3rd-party service failures/downtime.

6. **Quality Assurance:** How do you evaluate the agent before deployment? How do you verify that the generated reports answer users' intent correctly? How do you evaluate UX?

7. **Observability:** We need to know when the agent is failing and why. Define the metrics you would track at the agent level and how you would support deep-dive analysis/debugging (understanding what the message correspondence is and what went wrong).

8. **Agility (Persona Management):** The CEO wants to change the "tone" of the reports weekly. The system must allow non-developers to update the agent's instructions without redeployment.

---

## Deliverables

1. **Architecture Diagram (Mermaid etc.):** Highlighting the building blocks, services, compute and flow of the system based on the requirements above. In case you are planning to use an infrastructure/framework/service/data store for one of the building blocks, specify which one and include an explanation.

2. **Detailed technical explanation covering:**
   1. Reasoning for the chosen Cloud services / LLM models / frameworks used.
   2. Data flow between components (if needed, elaborate on HLD).
   3. Error handling and fallback strategies.
   4. Setup instructions and example run.
   5. Make sure to include a detailed explanation of how you handle/solve each of the requirements.

3. **Working Code/Prototype:** Build a chat agent that allows executives to ask data-related questions naturally, discuss them, and create a report with action items when asked to. The prototype needs to support at least 2 of the following requirements (defined above):
   1. Safety & PII Masking
   2. High-Stakes Oversight
   3. Resilience & Graceful Error Handling
   4. Quality Assurance
   5. Observability

4. **CLI-based interface** for chat interactions.

5. Your solution must be **runnable on another machine** (Docker is not a must, just proper setup instructions).

6. Use a **framework of your choice**.

> Our assessment will focus mainly on system design, the technical explanation, and an elegant prototype that fits the prototype requirements.

---

## Dataset Specification

- **Dataset:** `bigquery-public-data.thelook_ecommerce`
- **Required Tables:**
  - `orders` – Customer order information
  - `order_items` – Individual items within orders
  - `products` – Product catalog and details
  - `users` – Customer demographics and information

### Expected Agent Capabilities

Your prototype chat agent should be able to perform data analysis and generate insightful reports such as:

- Customer behavior (e.g., top customers, total spend)
- Product performance (e.g., compare performance of product X and Y, and why they perform differently)
- Time-based metrics (e.g., monthly revenue, up-to-date revenue by product)
- Answer questions about the general structure of the database (what data is available, what can we do with it)
- Multi-step queries (as described above)

1. Use BigQuery integration to query and analyze the specified tables. Your agent should be able to construct and execute SQL queries dynamically based on the analysis requirements.
2. You should preferably use one of the newer Google Gemini models. You can get a free API key from Google AI Studio. Please be mindful of the rate limits. Alternatively, you can use OpenRouter or Ollama if you prefer (or have issues with rate limits). We have created a simple client for your convenience: https://github.com/Opsfleet/lc-openrouter-ollama-client

---

## Setup Instructions

### Environment Setup

1. Install Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

### GCP/BigQuery Setup

1. Set up BigQuery access by following the BigQuery Client Libraries documentation if you don't already have BigQuery access configured.
2. **Free Tier:** Google Cloud provides 1 TB of free BigQuery compute per month, which is more than sufficient for this challenge.
3. **Authentication:** Ensure your environment is authenticated with Google Cloud to access the public datasets.

---

## Time Expectation

We expect this assignment to take between **6 to 12 hours** of work.

## Submission

Share with us your public GitHub repository with:

- Documentation
- Source code
- Architecture diagram
