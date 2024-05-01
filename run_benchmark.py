#!/usr/bin/env python

import os
import csv
import json
import time
import pickle
import openai
import pandas as pd
from pathlib import Path
from tqdm import tqdm
from dotenv import load_dotenv
from mech.packages.valory.customs.prediction_request import prediction_request
from benchmark.utils import get_logger, TokenCounterCallback

load_dotenv()
logger = get_logger(__name__)
this_dir = Path(__file__).parent


def tool_map(tool):
    """Map the tool name to the tool class."""

    tool_dict = {
        "prediction-online": prediction_request,
        "prediction-offline": prediction_request,
    }

    tool = tool_dict.get(tool, None)

    if tool is None:
        raise Exception(f"Tool {tool} not found.")
    else:
        return tool


def prepare_questions(kwargs):
    test_questions = json.load(
        open(this_dir / "olas-predict-benchmark/benchmark/data/autocast/autocast_questions_filtered.json")
    )
    with open(
        this_dir / "olas-predict-benchmark/benchmark/data/autocast/autocast_questions_filtered.pkl", "rb"
    ) as f:
        url_to_content = pickle.load(f)
    num_questions = kwargs.pop("num_questions", len(test_questions))

    questions = []
    for q in test_questions:
        if q["qtype"] == "t/f" and q["answer"] is not None:
            questions.append(q)
        if len(questions) >= num_questions:
            break

    return questions, url_to_content


def parse_response(response, test_q):
    try:
        result = json.loads(response[0])
    except Exception as e:
        print("The response is not json-format compatible")
        print(f"################### response[0] = {response[0]}")
        test_q["Correct"] = False
        test_q["prediction"] = None
        return test_q

    if "p_yes" in result.keys():
        test_q["p_yes"] = float(result["p_yes"])
    else:
        test_q["p_yes"] = None

    if "p_no" in result.keys():
        test_q["p_no"] = float(result["p_no"])
    else:
        test_q["p_no"] = None
    
    if "confidence" in result.keys():
        test_q["confidence"] = float(result["confidence"])
    else:
        test_q["confidence"] = None

    if "info_utility" in result.keys():
        test_q["info_utility"] = float(result["info_utility"])
    else:
        test_q["info_utility"] = None

    if response[3] is not None:
        test_q["input_tokens"] = response[3].cost_dict["input_tokens"]
        test_q["output_tokens"] = response[3].cost_dict["output_tokens"]
        test_q["total_tokens"] = response[3].cost_dict["total_tokens"]
        test_q["input_cost"] = response[3].cost_dict["input_cost"]
        test_q["output_cost"] = response[3].cost_dict["output_cost"]
        test_q["total_cost"] = response[3].cost_dict["total_cost"]
    test_q["prompt_response"] = response[1].replace(os.linesep, "")

    if (test_q["p_yes"] is None) or (float(result["p_yes"]) == float(result["p_no"])):
        test_q["prediction"] = None
    else:
        test_q["prediction"] = "yes" if test_q["p_yes"] > test_q["p_no"] else "no"
    test_q["Correct"] = test_q["prediction"] == test_q["answer"]
    return test_q


def write_results(csv_file_path):

    results_path = Path(csv_file_path.parent)
    time_string = csv_file_path.stem.split("_", 1)[-1]

    results_df = pd.read_csv(csv_file_path)
    num_errors = results_df["error"].count()
    logger.info(f"Num errors: {str(num_errors)}")
    results_df = results_df.dropna(subset=["prediction"])
    grouped_df = results_df.groupby(["tool", "model"]).agg(
        {
            "Correct": ["mean", "sum", "count"],
            "crowd_correct": ["mean"],
            "input_tokens": ["mean"],
            "output_tokens": ["mean"],
            "total_tokens": ["mean"],
            "input_cost": ["mean"],
            "output_cost": ["mean"],
            "total_cost": ["mean"],
        }
    )

    grouped_df.columns = ["_".join(col).strip() for col in grouped_df.columns.values]
    summary_df = grouped_df.reset_index().rename(
        columns={
            "Correct_mean": "accuracy",
            "Correct_sum": "correct",
            "Correct_count": "total",
            "crowd_correct_mean": "crowd_accuracy",
        }
    )

    logger.info(f"Results:\n\n {results_df}")
    summary_df.to_csv(results_path / f"summary_{time_string}.csv", index=False)


def run_benchmark(kwargs):
    """Start the benchmark tests. If a category flag is provided, run the categories with that mark."""

    logger.info("Running benchmark tests...")

    tools = kwargs.pop("tools")
    model = kwargs.pop("model")[0]
    MAX_RETRIES = kwargs.pop("max_retries", 3)
    questions, url_to_content = prepare_questions(kwargs)
    logger.info(f"Running {len(questions)} questions for each tool: {tools}")

    results_path = Path("results")
    if not results_path.exists():
        results_path.mkdir(exist_ok=True)

    start_time = time.time()
    time_string = time.strftime("%y%m%d%H%M%S", time.localtime(start_time))
    csv_file_path = results_path / f"results_{time_string}.csv"

    logger.info("Creating csv files...")
    with open(csv_file_path, mode="a", newline="") as file:
        fieldnames = [
            "prompt",
            "answer",
            "tool",
            "model",
            "p_yes",
            "p_no",
            "confidence",
            "info_utility",
            "prediction",
            "Correct",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "input_cost",
            "output_cost",
            "total_cost",
            "prompt_response",
            "error",
            "crowd_prediction",
            "crowd_correct",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)

        if file.tell() == 0:
            writer.writeheader()

        for t in tools:
            logger.info("Loading the tool...")
            try:
                tool = tool_map(t)
            except Exception as e:
                logger.error(f"Error while loading the tool={tool}")
                continue
            correct_answers = 0
            total_answers = 0
            for test_question in tqdm(
                questions, desc=f"Running tool {t}", total=len(questions)
            ):
                test_q = {
                    "prompt": test_question["question"],
                    "answer": test_question["answer"],
                    "crowd_prediction": test_question["crowd"][-1]["forecast"],
                    "tool": t,
                    "model": model,
                    "counter_callback": TokenCounterCallback(),
                    "prompt_response": None,
                }

                if kwargs["provide_source_links"]:
                    test_q["source_links"] = test_question["source_links"]
                    test_q["source_links"] = {
                        source_link: url_to_content[source_link]
                        for source_link in test_q["source_links"]
                    }

                crowd_forecast = test_question["crowd"][-1]["forecast"]
                test_q["crowd_prediction"] = (
                    "yes"
                    if crowd_forecast > 0.5
                    else "no" if crowd_forecast < 0.5 else None
                )
                test_q["crowd_correct"] = test_q["crowd_prediction"] == test_q["answer"]

                CURRENT_RETRIES = 0
                while True:
                    try:
                        response = tool.run(**{**test_q, **kwargs})
                        test_q = parse_response(response, test_q)
                        if test_q["Correct"] == True:
                            correct_answers += 1
                        if test_q["prediction"] is not None:
                            total_answers += 1
                            print(
                                f"===========ACCURACY============== {correct_answers/total_answers*100}%"
                            )
                        break
                    except openai.APIError as e:
                        logger.error(f"Error running benchmark for tool {t}: {e}")
                        CURRENT_RETRIES += 1
                        if CURRENT_RETRIES > MAX_RETRIES:
                            logger.error(
                                f"Max retries reached for tool {t}. Skipping question."
                            )
                            test_q["error"] = e
                            break
                        else:
                            logger.info(
                                f"Retrying tool {t} for question {test_q['prompt']}"
                            )
                            continue

                    except Exception as e:
                        logger.error(f"Error running benchmark for tool {t}: {e}")
                        test_q["error"] = e
                        break

                if kwargs["provide_source_links"]:
                    del test_q["source_links"]
                del test_q["counter_callback"]

                writer.writerow(test_q)

    write_results(csv_file_path)

    end_time = time.time()
    total_time = end_time - start_time
    logger.info(f"Total Time: {total_time} seconds")


if __name__ == "__main__":
    kwargs = {}
    kwargs["num_questions"] = 10
    kwargs["tools"] = [
        "prediction-online",
    ]
    kwargs["model"] = [
        "gpt-3.5-turbo-0125",
    ]
    kwargs["api_keys"] = {}
    kwargs["api_keys"]["openai"] = os.getenv("OPENAI_API_KEY")
    kwargs["api_keys"]["anthropic"] = os.getenv("ANTHROPIC_API_KEY")
    kwargs["api_keys"]["openrouter"] = os.getenv("OPENROUTER_API_KEY")
    kwargs["api_keys"]["google_api_key"] = os.getenv("GOOGLE_API_KEY")
    kwargs["api_keys"]["google_engine_id"] = os.getenv("GOOGLE_ENGINE_ID")
    kwargs["api_keys"]["tavily"] = os.getenv("TAVILY_API_KEY")

    kwargs["num_urls"] = 3
    kwargs["num_words"] = 300
    kwargs["provide_source_links"] = True
    run_benchmark(kwargs)
