import os
import subprocess

def run_command(command, shell=True):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=shell)
    stdout, stderr = process.communicate()

    if process.returncode == 0:
        print("Command executed successfully")
        print(stdout.decode())
    else:
        print("Command failed")
        print(stderr.decode())

def prepare_benchmark():
    # check if the directory exists
    if not os.path.exists("olas-predict-benchmark"):
        run_command("git clone https://github.com/valory-xyz/olas-predict-benchmark.git")
        run_command("cd olas-predict-benchmark && git submodule update --init --recursive")
        run_command("cd olas-predict-benchmark/benchmark/ && poetry install")
        run_command("mkdir olas-predict-benchmark/benchmark/data")
        run_command("cd olas-predict-benchmark/benchmark/data && git clone https://huggingface.co/datasets/valory/autocast")
    else:
        run_command("cd olas-predict-benchmark && git submodule update --remote --recursive")
        run_command("cd olas-predict-benchmark/benchmark/ && poetry install")


def run_benchmark():
    prepare_benchmark()
    run_command("source olas-predict-benchmark/benchmark/.venv/bin/activate && python run_benchmark.py")



if __name__ == "__main__":
    run_benchmark()