USER_CONTENT_TEMPLATE = """
Generate a Python script to perform a single video editing task.

**Full Task Context (for your information only):**
{context}

**History of Previously Executed Scripts in this Chain:**
This section shows the *full source code* of scripts that have already been successfully run in this task chain.
The files listed in the 'outputs' of a previous step are available as 'inputs' for the current step.
---
{script_history}
---

**Inputs for THIS specific step:**
{inputs}

**Outputs for THIS specific step:**
{outputs}

**Instruction for THIS script:**
'{task}'

The script must be complete, executable Python code.
It must read from the specified input file(s) and write to the specified output file(s).
The script will be executed in a directory where the input files are present.
Use the `subprocess` module to execute commands.
IMPORTANT: Do NOT use `sys.exit()`. Raise exceptions for error handling.
"""