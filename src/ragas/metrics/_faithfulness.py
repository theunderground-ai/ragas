from __future__ import annotations

import typing as t
from dataclasses import dataclass

import numpy as np
from langchain.callbacks.manager import CallbackManager, trace_as_chain_group
from langchain.prompts import ChatPromptTemplate, HumanMessagePromptTemplate

from ragas.metrics.base import EvaluationMode, MetricWithLLM
from ragas.utils import json_loader

if t.TYPE_CHECKING:
    from datasets import Dataset
    from langchain.callbacks.base import Callbacks


LONG_FORM_ANSWER_PROMPT = HumanMessagePromptTemplate.from_template(
    """\
Create one or more statements from each sentence in the given answer.

question: Who was  Albert Einstein and what is he best known for?
answer: He was a German-born theoretical physicist, widely acknowledged to be one of the greatest and most influential physicists of all time. He was best known for developing the theory of relativity, he also made important contributions to the development of the theory of quantum mechanics.
statements in json:
{{
    "statements": [
        "Albert Einstein was born in Germany.",
        "Albert Einstein was best known for his theory of relativity."
    ]
}}

question: Cadmium Chloride is slightly soluble in this chemical, it is also called what?
answer: alcohol
statements in json:
{{
    "statements": [
        "Cadmium Chloride is slightly soluble in alcohol."
    ]
}}

question: Were Hitler and Benito Mussolini of the same nationality?
answer: Sorry, I can't provide answer to that question.
statements in json:
{{
    "statements": []
}}

question:{question}
answer: {answer}
statements in json:"""  # noqa: E501
)


NLI_STATEMENTS_MESSAGE = HumanMessagePromptTemplate.from_template(
    """
 Natural language inference. Only use "Yes" or "No" as verdict.

Context:
John is a student at XYZ University. He is pursuing a degree in Computer Science. He is enrolled in several courses this semester, including Data Structures, Algorithms, and Database Management. John is a diligent student and spends a significant amount of time studying and completing assignments. He often stays late in the library to work on his projects.
statement_1: John is majoring in Biology.
statement_2: John is taking a course on Artificial Intelligence. 
statement_3: John is a dedicated student. 
statement_4: John has a part-time job.
Answer:
[
    {{
        "statement_1": "John is majoring in Biology.",
        "reason": "John's major is explicitly mentioned as Computer Science. There is no information suggesting he is majoring in Biology.",
        "verdict": "No"
    }},
    {{
        "statement_2": "John is taking a course on Artificial Intelligence.",
        "reason": "The context mentions the courses John is currently enrolled in, and Artificial Intelligence is not mentioned. Therefore, it cannot be deduced that John is taking a course on AI.",
        "verdict": "No"
    }},
    {{
        "statement_3": "John is a dedicated student.",
        "reason": "The context states that he spends a significant amount of time studying and completing assignments. Additionally, it mentions that he often stays late in the library to work on his projects, which implies dedication.",
        "verdict": "Yes"
    }},
    {{
        "statement_4": "John has a part-time job.",
        "reason": "There is no information given in the context about John having a part-time job.",
        "verdict": "No"
    }}
]

Context:
Photosynthesis is a process used by plants, algae, and certain bacteria to convert light energy into chemical energy.
statement_1: Albert Einstein was a genius.
Answer:
[
     {{
        "statement_1": "Albert Einstein was a genius.",
        "reason": "The context and statement are unrelated"
        "verdict": "No"
    }}
]

Context:
Albert Einstein was a German-born theoretical physicist who is widely held to be one of the greatest and most influential scientists of all time.
statement_1: Nil
Answer:
[
     {{
        "statement_1": "Nil",
        "reason": "The statement is invalid",
        "verdict": "No"
    }}
]


context:
{context}
statements:
{statements}
Answer:
"""  # noqa: E501
)


@dataclass
class Faithfulness(MetricWithLLM):
    name: str = "faithfulness"  # type: ignore
    evaluation_mode: EvaluationMode = EvaluationMode.qac  # type: ignore
    batch_size: int = 15

    def _score_batch(
        self: t.Self,
        dataset: Dataset,
        callbacks: t.Optional[Callbacks] = None,
        callback_group_name: str = "batch",
    ) -> list[float]:
        """
        returns the NLI score for each (q, c, a) pair
        """

        question, answer, contexts = (
            dataset["question"],
            dataset["answer"],
            dataset["contexts"],
        )
        prompts = []

        cb = CallbackManager.configure(inheritable_callbacks=callbacks)
        with trace_as_chain_group(
            callback_group_name, callback_manager=cb
        ) as batch_group:
            for q, a in zip(question, answer):
                human_prompt = LONG_FORM_ANSWER_PROMPT.format(question=q, answer=a)
                prompts.append(ChatPromptTemplate.from_messages([human_prompt]))

            result = self.llm.generate(prompts, callbacks=batch_group)

            prompts = []
            for context, output in zip(contexts, result.generations):
                statements = json_loader.safe_load(output[0].text, self.llm).get(
                    "statements", []
                )
                statements = statements if statements != [] else ["Nil"]
                statements_str: str = "\n".join(
                    [f"statement_{i+1}: {st}" for i, st in enumerate(statements)]
                )
                contexts_str: str = "\n".join(context)
                human_prompt = NLI_STATEMENTS_MESSAGE.format(
                    context=contexts_str, statements=statements_str
                )
                prompts.append(ChatPromptTemplate.from_messages([human_prompt]))

            result = self.llm.generate(prompts, callbacks=batch_group)
            outputs = result.generations
            verdict_score_map = {"yes": 1, "no": 0, "null": np.nan}
            scores = []
            for output in outputs:
                # json fixing is unlikely to work because there are any number of things that can go wrong, from not json at all, to a missing comma
                if False and output[0].text.find('"verdict": "Yes') > -1:
                    verdict = json_loader.safe_load(output[0].text, self.llm)
                    if isinstance(verdict, dict) and "verdict" in verdict:
                        verdict = [verdict]

                    if isinstance(verdict, list):
                        faithful_statements = sum(
                            verdict_score_map.get(dict.get("verdict", "").lower(), np.nan)
                            for dict in output
                        )
                        num_statements = len(output)
                        if num_statements:
                            score = faithful_statements / num_statements
                        else:
                            score = np.nan

                    
                    scores.append(score)
                else:
                    # output = output if isinstance(output, list) else [output]
                    is_true = output[0].text.__contains__('statement is true') or output[0].text.find('"verdict": "Yes') > -1

                    # faithful_statements = sum(
                    #     verdict_score_map.get(dict.get("verdict", "").lower(), np.nan)
                    #     for dict in output
                    # )
                    # num_statements = len(output)
                    # if num_statements:
                    score = 1 if is_true else 0
                    scores.append(score)

        return scores


faithfulness = Faithfulness()
