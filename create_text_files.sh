#!/bin/bash

# Delete the directory "tmp_text" if it exists
if [ -d "tmp_text" ]; then
  rm -rf "tmp_text"
fi

# Create the directory if it does not exist
mkdir -p "tmp_text"

# Create 1000 text files, each with only 1 word
for i in $(seq 1 1000); do
  echo "word$i" > "tmp_text/file$i.txt"
done
