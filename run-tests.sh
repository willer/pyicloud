#!/bin/bash

echo "Running Integration Tests..."
echo "Make sure you have created a 'Tests' list in your Reminders app before running these tests."
echo "--------------------------------------------"

echo "Running Reminders Tests..."
python -m pytest tests_integration/test_reminders_integration.py -v -s

echo -e "\nRunning Notes Tests..."
python -m pytest tests_integration/test_notes_integration.py -v -s

