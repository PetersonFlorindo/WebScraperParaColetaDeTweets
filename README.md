# X/Twitter Web Scraper for NLP

This project contains a Python script created as a free alternative to the paid X/Twitter API for collecting tweets.

It was developed to capture public reactions on social media to movies during the pre-release window, making it useful for analyzing buzz, initial audience perception and sentiment before theatrical release. Although originally designed for the movie industry, the script can be adapted to different contexts, such as brands, products, events, market research and other NLP applications.

## Objective

Collect tweets related to specific movies, topics or keywords and export them as structured CSV files for sentiment analysis, market studies, public opinion analysis and other Natural Language Processing tasks.

## Features

* Collects tweets without using the official X/Twitter API
* Uses Nitter as a public search interface
* Automates navigation with Selenium and PyAutoGUI
* Searches tweets within a defined time window
* Generates keyword variations automatically
* Exports collected data to CSV files
* Saves progress to allow collection to be resumed later
* Removes duplicated tweets based on URL

## Prerequisites

Install the required Python libraries:

```bash
pip install selenium pyautogui
```

You also need Google Chrome installed and ChromeDriver properly configured.

## Input File

The script expects a CSV file named:

```text
details_valid.csv
```

The file should contain columns such as:

```csv
tmdb_id,title,release_date,budget_usd
```

Example:

```csv
tmdb_id,title,release_date,budget_usd
12345,Example Movie,2024-05-10,50000000
```

## Usage

Run the script:

```bash
python WebScraper.py
```

Then enter how many titles you want to collect in the current execution:

```text
Quantos títulos deseja coletar nesta execução?
```

## Output

For each processed title, the script generates a CSV file following this structure:

```text
tweets_<tmdb_id>_<title>.csv
```

The output file contains the following columns:

```csv
query,tweet_date,username,name,text,url
```

## Applications

* Sentiment analysis
* Market research
* Social media buzz monitoring
* Public opinion analysis
* NLP datasets
* Predictive modeling with text data

## Notes

This scraper depends on the HTML structure and availability of Nitter, which may change over time. The script may require adjustments if Nitter changes its page structure or if access becomes unstable.

The project does not use the official X/Twitter API.

## Usage and Authorship

This project was developed by Peterson Oliveira Florindo.

The use, study, adaptation and sharing of this code are allowed, as long as proper credit is given to the author. This project is made available for educational, academic and research purposes.
