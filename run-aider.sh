#!/bin/bash

#aider --architect --model=openrouter/deepseek/deepseek-r1 --editor-model=sonnet --no-auto-commits --yes-always --test-cmd=./run-tests.sh --auto-test
aider --architect --model=o3-mini --editor-model=sonnet --no-auto-commits --yes-always --test-cmd=./run-tests.sh --auto-test

