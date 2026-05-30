# Data Engineering & Extraction Pipelines

This repository contains a collection of data engineering projects focused on scraping, processing, classifying, and analyzing academic and extracurricular data. Each top-level folder represents a distinct data project or a shared resource.

## Repository Structure

The repository is organized into top-level folders for each major data project or shared resource.

```
data_engineering/
├── activity_finder/
├── config/
├── dashboard_react/
├── logo/
├── masters/
├── phd/
├── professors/
├── prompts/
└── ug/
```

*   **Project Folders** (`activity_finder`, `ug`, etc.): Each contains the specific scripts and documentation for that data vertical.
*   **Shared Folders** (`config`, `prompts`): Contain configuration files and prompts that are used by one or more projects.
*   **Frontend** (`dashboard_react`): A self-contained web application for data visualization.


## Table of Contents

1.  [🚀 Getting Started](#-getting-started)
2.  [📂 Activity Finder](#-activity-finder)
3.  [📂 Dashboard React App](#-dashboard-react-app)
4.  [📂 Logo Extraction](#-logo-extraction)
5.  [📂 Masters Programs](#-masters-programs)
6.  [📂 PhD Programs](#-phd-programs)
7.  [📂 Professor Information](#-professor-information)
8.  [📂 Prompts](#-prompts)
9.  [📂 Undergraduate Programs](#-undergraduate-programs)

---

## 🚀 Getting Started

This guide explains how to set up your environment to run the Python scripts in this repository.

### 1. Prerequisites

*   **Python:** Ensure you have Python 3.8 or newer installed.
*   **Pip:** Python's package installer, which comes with modern Python installations.

### 2. Environment Setup

It is highly recommended to use a Python virtual environment to manage project dependencies and avoid conflicts.

**a. Create a virtual environment:**
Open a terminal in the root of the `Data_Engineering` repository and run:
```bash
python -m venv venv
```
This creates a `venv` folder that will contain all the necessary Python packages for this project.

**b. Activate the virtual environment:**
On Windows, run:
```bash
.\venv\Scripts\activate
```
Your terminal prompt should now be prefixed with `(venv)`, indicating the environment is active.

### 3. Installation

The Python scripts rely on external libraries. These should be listed in a `requirements.txt` file.

**a. Install dependencies:**
Once the `requirements.txt` file exists, you can install everything needed by running:
```bash
pip install -r requirements.txt
```
*(Note: If a `requirements.txt` file does not exist, we will need to create one by identifying the libraries used by the scripts, such as `google-cloud-aiplatform`, `pandas`, etc.)*

### 4. Running a Pipeline

Once set up, you can run a specific data pipeline script.

**Example of running the PhD pipeline:**
```bash
# Navigate to the project's script directory
cd data_engineering/phd/scripts

# Run the main pipeline script with appropriate arguments
python run_gemini_pipeline.py --input "path/to/input.csv" --output "path/to/output_dir"
```

### 5. Environment Variables for Secrets

Many scripts require API keys (e.g., for Gemini or AWS). These should be stored securely and **never** be committed to Git.

**a. Create a `.env` file:**
In the root of the `Data_Engineering` repository, create a file named `.env`.

**b. Add your secrets:**
Add your keys to this file in `KEY=VALUE` format. For example:
```
GOOGLE_APPLICATION_CREDENTIALS="path/to/your/gcp-credentials.json"
LOGODEV_SECRET_KEY="your_logodev_api_key_here"
```

**c. Add `.env` to `.gitignore`:**
Ensure the `.gitignore` file in the root of your repository contains a line with `.env` to prevent this file from ever being uploaded.

---

### 📂 Activity Finder

This project is a collection of data pipelines designed to find, categorize, and structure extracurricular activities for students. It is broken down into five main categories:

*   **Competitions:** Scripts and reports related to academic and extracurricular competitions.
*   **Internships:** Scripts for finding and vetting internship opportunities suitable for high school students.
*   **Olympiads:** Scripts and data related to various national and international academic Olympiads.
*   **Summer Schools:** Scripts for discovering and detailing pre-college summer programs.
*   **Volunteering:** Scripts focused on finding and structuring volunteering opportunities.

### 📂 Dashboard React App

This folder contains the source code for a **Next.js** web application designed to serve as a data monitoring and visualization dashboard for the various data projects in this repository.

*   **Purpose:** To provide a user interface for exploring, filtering, and verifying the quality of the extracted Masters, PhD, and Professor data.
*   **Key Files:**
    *   `src/`: Contains all the React components, application logic, and pages.
    *   `package.json`: Defines the project dependencies and run scripts (`npm run dev`).
    *   `next.config.ts`: Configuration file for the Next.js framework.
*   **Note:** This is a self-contained web application. To run it, you will need to install its dependencies using `npm install` and then start the development server.

### 📂 Logo Extraction

This project is dedicated to a single task: extracting official logo URLs from a list of domains.

*   **Purpose:** To programmatically find the best-quality logo for a given university, company, or organization to be used in frontend applications.
*   **Key Scripts:**
    *   `url_context.py`: The main script that uses the Gemini API to visit a URL and intelligently find the logo by analyzing the HTML structure.
    *   `check_favicons.py`: A utility script to verify the validity of found logo URLs.

### 📂 Masters Programs

A data pipeline for scraping and classifying Masters (M.S., M.A., etc.) program data from university websites.

*   **Purpose:** To build a structured database of postgraduate Masters programs, including admission requirements, deadlines, and tuition information.
*   **Key Scripts:**
    *   `classify_masters.py`: The main script for classifying the structured data.
    *   `run_gemini_pipeline.py` (inside `masters_v2/scripts`): The core V2 pipeline script for running the end-to-end data extraction.
    *   `crawl_masters_urls.py` (inside `official_urls`): A script dedicated to crawling the initial list of program URLs.

### 📂 PhD Programs

A data pipeline for scraping and classifying PhD (Doctoral) program data. This project focuses on details critical to PhD applicants, such as funding and faculty research.

*   **Purpose:** To build a structured database of doctoral programs with a focus on faculty, research areas, funding, and application requirements.
*   **Key Scripts:**
    *   `classify_phd.py` & `classify_fees_phd.py`: Core scripts for classifying extracted PhD program data and their associated fees.
    *   `run_gemini_pipeline.py` (inside `phd_v2/scripts`): The main V2 pipeline script for running the end-to-end data extraction.
    *   `generate_review_sheet_phd.py`: A utility to create Excel sheets for manual review of low-confidence data.

### 📂 Professor Information

A large, multi-stage project dedicated to extracting detailed information about university professors from their faculty and department pages.

*   **Purpose:** To build a dataset of academic professionals, including their research interests, publications, and contact information.
*   **Key Scripts:**
    *   `stage1_unwrap.py`, `stage2_probe.py`, `stage3_report.py`: A series of scripts that define the main multi-stage processing pipeline.
    *   `professor_extract_runner.py`: The core runner script for the main data extraction logic.
*   **Configuration:** This project uses a dedicated `config` folder containing CSV files (e.g., `universities_top_450.csv`) to define its input.

### 📂 Prompts

This is a crucial, shared resource folder. It does not contain executable scripts but rather the text and configuration files that are fed *into* the scripts.

*   **Purpose:** To centralize the prompt engineering for the various Gemini-based scrapers. This allows for easy updating and versioning of the prompts without changing the Python code.
*   **Contains:** A series of `.txt` files (the prompts) and corresponding `.json` files (the configurations) for projects like Competitions, Internships, and Olympiads.

### 📂 Undergraduate Programs

A data pipeline for scraping and classifying Undergraduate (B.S., B.A., etc.) program data.

*   **Purpose:** To build a structured database of undergraduate programs, including details scraped from university websites and other sources like BigFuture.
*   **Key Scripts:**
    *   `run_ug_pipeline.py`: The main script to execute the UG data processing pipeline.
    *   `classify_ug.py`: The main script for classifying the structured data.
    *   `scrape_bigfuture.py`: A specific scraper for gathering data from the BigFuture platform.
