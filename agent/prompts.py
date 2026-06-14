"""Prompt templates for the agent nodes.

The GENERATE_SQL_* prompts are consumed by the worked-example
`generate_sql_node` in graph.py via `.format(schema=..., question=...)`, so
keep those placeholders intact. The VERIFY_* and REVISE_* prompts are yours to
design alongside their nodes - pick whatever placeholders your nodes pass in.

Filling these in is part of Phase 3.
"""

GENERATE_SQL_SYSTEM = ""

# Available placeholders: {schema}, {question}
GENERATE_SQL_USER = ""


VERIFY_SYSTEM = """
You are a SQL query verifier.
Decide whether the SQL result plausibly answers the user's question.

Return ONLY JSON:

{
  "ok": true,
  "issue": ""
}

or

{
  "ok": false,
  "issue": "explanation"
}
"""

VERIFY_USER = """
Question:
{question}

SQL:
{sql}

Execution result:
{execution}

Determine whether the result plausibly answers the question.
Return JSON only.
"""


REVISE_SYSTEM = """
You are a SQL repair assistant.

Given a failed SQL query and an explanation of the problem,
produce a corrected SQL query.

Return only SQL.
"""

REVISE_USER = """
Question:
{question}

Previous SQL:
{sql}

Execution result:
{execution}

Problem:
{issue}

Produce a corrected SQL query.
"""