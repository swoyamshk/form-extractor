from flask import Flask, request, jsonify, render_template, send_file
import requests
from bs4 import BeautifulSoup
import json
import csv
import os
import re
import io
from datetime import datetime
import logging
from flask_cors import CORS

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

def extract_form_data(form_url):
    """
    Extract questions, points, options, correct answers, user answers, and image URLs from a Google Form score view
    """
    # Set up session with browser-like headers
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    })

    # Fetch the page
    try:
        response = session.get(form_url)
        response.raise_for_status()
        logger.info(f"Successfully fetched the form page. Content length: {len(response.text)}")
    except requests.RequestException as e:
        logger.error(f"Failed to access the form. Error: {str(e)}")
        return {"error": f"Failed to access the form. Error: {str(e)}"}

    # Parse the page
    soup = BeautifulSoup(response.text, 'html.parser')

    # Initialize results
    results = {
        'title': "Google Form Responses",
        'questions': []
    }

    # Extract and clean form title from HTML
    title_div = soup.find('div', class_='cTDvob')
    if title_div:
        title_text = title_div.get_text().strip()
        results['title'] = re.sub(r'\s*\*+\s*', '', title_text).strip()
        logger.debug(f"Extracted form title: {results['title']}")

    # Extract form data from script (for questions and options)
    form_data = None
    for script in soup.find_all('script'):
        if script.string and "var FB_PUBLIC_LOAD_DATA_" in script.string:
            json_text = re.search(r'var FB_PUBLIC_LOAD_DATA_ = (.*);', script.string)
            if json_text:
                try:
                    form_data = json.loads(json_text.group(1))
                    logger.info("Successfully extracted form data JSON")
                    break
                except json.JSONDecodeError as e:
                    logger.error(f"Error parsing form data: {str(e)}")
                    return {"error": f"Error parsing form data: {str(e)}"}

    if not form_data:
        logger.error("Could not find form data in the page")
        return {"error": "Could not find form data in the page"}

    # Extract questions and options from JSON data
    if len(form_data) > 1 and len(form_data[1]) > 1:
        for item in form_data[1][1]:
            if len(item) < 4:
                continue

            question_text = item[1] if len(item) > 1 else "Unknown Question"
            
            # Identify if this is a section break or video
            question_type = item[3] if len(item) > 3 else 0
            is_section_or_video = (question_type == 8 or 
                                  question_text.lower().strip() == 'video' or
                                  (not question_type and question_text.upper() == question_text))
            
            # Options
            options = []
            if len(item) > 4 and isinstance(item[4], list):
                for option_group in item[4]:
                    if isinstance(option_group, list) and len(option_group) > 1:
                        if isinstance(option_group[1], list):
                            for option in option_group[1]:
                                if isinstance(option, list) and len(option) > 0:
                                    options.append(str(option[0]))
                        elif isinstance(option_group[1], str):
                            options.append(option_group[1])

            # Correct answer (from JSON)
            correct_answer = None
            if len(item) > 4 and isinstance(item[4], list):
                for option_group in item[4]:
                    if isinstance(option_group, list) and len(option_group) > 3 and option_group[3] == 1:
                        if isinstance(option_group[1], list) and len(option_group[1]) > 0:
                            correct_answer = str(option_group[1][0][0]) if isinstance(option_group[1][0], list) else str(option_group[1][0])
                        elif isinstance(option_group[1], str):
                            correct_answer = option_group[1]
                        logger.debug(f"Question '{question_text}': Found correct answer from JSON: {correct_answer}")
                        break

            # Points possible (from JSON)
            points_possible = None if is_section_or_video else (str(item[3]) if len(item) > 3 and item[3] else "0")

            # Image URLs
            image_urls = []
            media_indices = [5, 6, 7]
            for idx in media_indices:
                if len(item) > idx and isinstance(item[idx], list):
                    for media_item in item[idx]:
                        if isinstance(media_item, list):
                            for subitem in media_item:
                                if isinstance(subitem, list):
                                    for potential_url in subitem:
                                        if isinstance(potential_url, str) and (
                                            'googleusercontent' in potential_url or
                                            potential_url.endswith(('.jpg', '.png', '.jpeg'))
                                        ):
                                            image_urls.append(potential_url)

            results['questions'].append({
                'question': question_text,
                'is_section_or_video': is_section_or_video,
                'points_possible': points_possible,
                'options': options,
                'correct_answer': None if is_section_or_video else correct_answer,
                'user_answer': None,
                'points_received': None,
                'is_correct': None,
                'image_urls': image_urls,
                'feedback': None
            })

    # Extract user responses from HTML
    question_items = soup.find_all('div', class_='Qr7Oae')
    logger.info(f"Found {len(question_items)} question items in HTML")

    for i, item in enumerate(question_items):
        while i >= len(results['questions']):
            results['questions'].append({
                'question': f"Unknown Question {i+1}",
                'is_section_or_video': False,
                'points_possible': "0",
                'options': [],
                'correct_answer': None,
                'user_answer': None,
                'points_received': None,
                'is_correct': None,
                'image_urls': [],
                'feedback': None
            })

        question_data = results['questions'][i]

        # Question text
        question_text_div = item.find('span', class_='M7eMe')
        if question_text_div:
            question_text = question_text_div.get_text().strip()
            question_data['question'] = question_text
            
            # Update is_section_or_video based on the text
            if (question_text.upper() == question_text and len(question_text.split()) <= 3) or question_text.lower() == 'video':
                question_data['is_section_or_video'] = True

        # User answer (skip for section/video)
        if not question_data['is_section_or_video']:
            user_answer_input = item.find('input', jsname='L9xHkb')
            if user_answer_input and 'value' in user_answer_input.attrs:
                user_answer = user_answer_input['value'].strip()
                question_data['user_answer'] = user_answer if user_answer else "No Response"
                logger.debug(f"Question {i+1}: Found user answer: {user_answer}")
            else:
                selected_option = item.find('div', class_='Od2TWd hYsg7c N2RpBe RDPZE', attrs={'aria-checked': 'true'})
                if selected_option:
                    answer_span = selected_option.find_next('span', class_='aDTYNe snByac kTYmRb OIC90c')
                    if answer_span:
                        user_answer = answer_span.get_text().strip()
                        question_data['user_answer'] = user_answer if user_answer else "No Response"
                        logger.debug(f"Question {i+1}: Found user answer via radio button: {user_answer}")
                else:
                    question_data['user_answer'] = "No Response"

        # Points (skip for section/video)
        if not question_data['is_section_or_video']:
            points_div = item.find('div', class_='RGoode')
            if points_div:
                points_text = points_div.get_text().strip()
                try:
                    if '/' in points_text:
                        received, possible = points_text.split('/')
                        received = re.sub(r'[^\d]', '', received.strip()) or "0"
                        possible = possible.strip()
                        question_data['points_received'] = received
                        if possible != question_data['points_possible']:
                            logger.warning(f"Question {i+1}: Points possible mismatch. HTML: {possible}, JSON: {question_data['points_possible']}. Using HTML value.")
                            question_data['points_possible'] = possible
                    else:
                        question_data['points_received'] = re.sub(r'[^\d]', '', points_text.strip()) or "0"
                    logger.debug(f"Question {i+1}: Parsed points: {points_text} -> Received: {question_data['points_received']}, Possible: {question_data['points_possible']}")
                except Exception as e:
                    logger.warning(f"Question {i+1}: Could not parse points: {points_text}. Error: {str(e)}")
                    question_data['points_received'] = "0"
            else:
                question_data['points_received'] = "0"

        # Correctness (skip for section/video)
        if not question_data['is_section_or_video']:
            correctness_div = item.find('div', class_='zS667')
            if correctness_div and 'aria-label' in correctness_div.attrs:
                correctness_label = correctness_div['aria-label'].strip()
                question_data['is_correct'] = correctness_label == 'सही'
                logger.debug(f"Question {i+1}: Correctness: {correctness_label} -> {question_data['is_correct']}")
            else:
                if question_data['correct_answer'] and question_data['user_answer'] and question_data['user_answer'] != "No Response":
                    question_data['is_correct'] = (
                        str(question_data['correct_answer']).lower().strip() == 
                        str(question_data['user_answer']).lower().strip()
                    )
                    logger.debug(f"Question {i+1}: Inferred correctness: {question_data['is_correct']}")
                else:
                    question_data['is_correct'] = None

        # Correct answer (from HTML if not in JSON or if incorrect) - skip for section/video
        if not question_data['is_section_or_video'] and (not question_data['correct_answer'] or question_data['is_correct'] is False):
            correct_answer_div = item.find('div', class_='D42QGf')
            if correct_answer_div:
                correct_answer_span = correct_answer_div.find('span', class_='aDTYNe snByac kTYmRb OIC90c')
                if correct_answer_span:
                    question_data['correct_answer'] = correct_answer_span.get_text().strip()
                    logger.debug(f"Question {i+1}: Found correct answer from HTML: {question_data['correct_answer']}")
        if not question_data['is_section_or_video'] and question_data['is_correct'] is True and not question_data['correct_answer'] and question_data['user_answer'] != "No Response":
            question_data['correct_answer'] = question_data['user_answer']
            logger.debug(f"Question {i+1}: Set correct answer to user answer: {question_data['correct_answer']}")

        # Feedback - keep for all question types including section/video
        feedback_div = item.find('div', class_='PcXV5e')
        if feedback_div:
            feedback_text = feedback_div.find('div', class_='sIQxvc')
            if feedback_text:
                question_data['feedback'] = feedback_text.get_text().strip()
                logger.debug(f"Question {i+1}: Found feedback: {question_data['feedback']}")

    return results

def create_csv_data(response_data):
    """
    Create CSV data in memory
    """
    output = io.StringIO()
    fixed_option_count = 4
    
    headers = [
        'Question', 
        'Option 1', 'Option 2', 'Option 3', 'Option 4',
        'Points', 'Correct Answer', 'Is Correct', 'Feedback', 'Image URLs'
    ]

    writer = csv.writer(output)
    writer.writerow(headers)

    for q in response_data['questions']:
        # Prepare options
        options = q.get('options', [])
        option_cols = options + [''] * (fixed_option_count - len(options))
        
        # For section breaks or videos, exclude points, correct answer and is_correct values
        if q.get('is_section_or_video', False):
            row = [
                q['question'],                      # Question
                *option_cols,                       # Options 1-4
                '',                                 # Points (empty for section/video)
                '',                                 # Correct Answer (empty for section/video)
                '',                                 # Is Correct (empty for section/video)
                q.get('feedback', ''),              # Feedback
                '; '.join(q.get('image_urls', []))  # Image URLs
            ]
        else:
            # For actual questions, include all fields
            row = [
                q['question'],                      # Question
                *option_cols,                       # Options 1-4
                q.get('points_possible', '0'),      # Points
                q.get('correct_answer', 'Unknown'), # Correct Answer
                'Yes' if q.get('is_correct') else 'No' if q.get('is_correct') is False else 'Unknown', # Is Correct
                q.get('feedback', ''),              # Feedback
                '; '.join(q.get('image_urls', []))  # Image URLs
            ]
        
        writer.writerow(row)
    
    return output.getvalue()

@app.route('/')
def index():
    return render_template('index.html')
    
@app.route('/api/extract', methods=['POST'])
def extract():
    data = request.get_json()
    if not data or 'form_url' not in data:
        return jsonify({'error': 'No form URL provided'}), 400
    
    form_url = data['form_url']
    
    try:
        result = extract_form_data(form_url)
        
        if 'error' in result:
            return jsonify(result), 400
            
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in extraction: {str(e)}")
        return jsonify({'error': f'Extraction failed: {str(e)}'}), 500

@app.route('/api/download-csv', methods=['POST'])
def download_csv():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    try:
        csv_data = create_csv_data(data)
        
        # Return CSV data as response
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = ''.join(c if c.isalnum() else '_' for c in data.get('title', 'Google_Form'))
        filename = f"{safe_title}_responses_{timestamp}.csv"
        
        # Create a binary stream
        bytes_data = csv_data.encode('utf-8')
        bytes_io = io.BytesIO(bytes_data)
        
        return send_file(
            bytes_io,
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Error creating CSV: {str(e)}")
        return jsonify({'error': f'CSV creation failed: {str(e)}'}), 500

if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    os.makedirs('templates', exist_ok=True)
    
    # Create template file
    with open(os.path.join('templates', 'index.html'), 'w', encoding='utf-8') as f:
        f.write('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Google Form Data Extractor</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            padding-top: 2rem;
            background-color: #f8f9fa;
        }
        .container {
            max-width: 800px;
        }
        .card {
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .hidden {
            display: none;
        }
        #loading {
            margin-top: 20px;
        }
        #results {
            margin-top: 20px;
        }
        .question-item {
            margin-bottom: 10px;
            padding: 10px;
            border-radius: 5px;
            background-color: #f1f1f1;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="card mb-4">
            <div class="card-header bg-primary text-white">
                <h2 class="mb-0">Google Form Data Extractor</h2>
            </div>
            <div class="card-body">
                <p class="card-text">Enter a Google Form viewscore URL to extract and download the response data.</p>
                
                <form id="extractForm">
                    <div class="mb-3">
                        <label for="formUrl" class="form-label">Google Form Viewscore URL:</label>
                        <input type="url" class="form-control" id="formUrl" required 
                               placeholder="https://docs.google.com/forms/d/.../viewscore?...">
                        <div class="form-text">Must be a URL to a form score view page.</div>
                    </div>
                    <button type="submit" class="btn btn-primary" id="extractBtn">Extract Data</button>
                </form>
            </div>
        </div>
        
        <div id="loading" class="text-center hidden">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
            <p>Extracting data from form, please wait...</p>
        </div>
        
        <div id="error" class="alert alert-danger hidden" role="alert"></div>
        
        <div id="results" class="card hidden">
            <div class="card-header bg-success text-white">
                <h3 id="formTitle" class="mb-0">Form Results</h3>
            </div>
            <div class="card-body">
                <div class="d-flex justify-content-between mb-3">
                    <h4 id="questionCount">Questions found: 0</h4>
                    <button class="btn btn-success" id="downloadCsv">Export to CSV</button>
                </div>
                
                <div class="accordion" id="questionsAccordion">
                    <!-- Questions will be inserted here -->
                </div>
            </div>
        </div>
    </div>

    <script>
        let formData = null;
        
        document.getElementById('extractForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const formUrl = document.getElementById('formUrl').value;
            const loadingDiv = document.getElementById('loading');
            const errorDiv = document.getElementById('error');
            const resultsDiv = document.getElementById('results');
            
            // Reset state
            errorDiv.classList.add('hidden');
            errorDiv.textContent = '';
            resultsDiv.classList.add('hidden');
            loadingDiv.classList.remove('hidden');
            
            try {
                const response = await fetch('/api/extract', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ form_url: formUrl }),
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    formData = data;
                    displayResults(data);
                } else {
                    throw new Error(data.error || 'Failed to extract data from the form');
                }
            } catch (error) {
                errorDiv.textContent = error.message;
                errorDiv.classList.remove('hidden');
            } finally {
                loadingDiv.classList.add('hidden');
            }
        });
        
        document.getElementById('downloadCsv').addEventListener('click', async function() {
            if (!formData) return;
            
            try {
                const response = await fetch('/api/download-csv', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(formData),
                });
                
                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.error || 'Failed to generate CSV');
                }
                
                // Get filename from content-disposition header if possible
                const contentDisposition = response.headers.get('content-disposition');
                let filename = 'form_data.csv';
                if (contentDisposition) {
                    const filenameMatch = contentDisposition.match(/filename="(.+)"/);
                    if (filenameMatch) {
                        filename = filenameMatch[1];
                    }
                }
                
                // Create a blob and download it
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                a.remove();
            } catch (error) {
                const errorDiv = document.getElementById('error');
                errorDiv.textContent = error.message;
                errorDiv.classList.remove('hidden');
            }
        });
        
        function displayResults(data) {
            document.getElementById('formTitle').textContent = data.title || 'Form Results';
            document.getElementById('questionCount').textContent = `Questions found: ${data.questions.length}`;
            
            const accordion = document.getElementById('questionsAccordion');
            accordion.innerHTML = '';
            
            data.questions.forEach((question, index) => {
                const itemId = `question-${index}`;
                const isSection = question.is_section_or_video;
                
                const accordionItem = document.createElement('div');
                accordionItem.className = 'accordion-item';
                
                const header = document.createElement('h2');
                header.className = 'accordion-header';
                
                const button = document.createElement('button');
                button.className = 'accordion-button collapsed';
                button.type = 'button';
                button.setAttribute('data-bs-toggle', 'collapse');
                button.setAttribute('data-bs-target', `#${itemId}`);
                button.setAttribute('aria-expanded', 'false');
                button.setAttribute('aria-controls', itemId);
                
                // Question title with type and correctness indicators
                let buttonContent = `Q${index + 1}: ${question.question}`;
                if (isSection) {
                    button.classList.add('bg-light', 'text-muted');
                    buttonContent += ' (Section/Video)';
                } else if (question.is_correct === true) {
                    button.classList.add('bg-success-subtle');
                    buttonContent += ' ✓';
                } else if (question.is_correct === false) {
                    button.classList.add('bg-danger-subtle');
                    buttonContent += ' ✗';
                }
                button.innerHTML = buttonContent;
                
                header.appendChild(button);
                accordionItem.appendChild(header);
                
                const collapseDiv = document.createElement('div');
                collapseDiv.id = itemId;
                collapseDiv.className = 'accordion-collapse collapse';
                
                const body = document.createElement('div');
                body.className = 'accordion-body';
                
                // Question details
                let detailsHTML = '';
                
                if (!isSection) {
                    // Points
                    if (question.points_possible) {
                        detailsHTML += `<p><strong>Points:</strong> ${question.points_received || '0'}/${question.points_possible}</p>`;
                    }
                    
                    // Options
                    if (question.options && question.options.length > 0) {
                        detailsHTML += '<p><strong>Options:</strong></p><ul>';
                        question.options.forEach(option => {
                            const isCorrect = option === question.correct_answer;
                            const isUserAnswer = option === question.user_answer;
                            
                            let optionClass = '';
                            let indicator = '';
                            
                            if (isCorrect && isUserAnswer) {
                                optionClass = 'text-success fw-bold';
                                indicator = ' ✓ (Your correct answer)';
                            } else if (isCorrect) {
                                optionClass = 'text-success';
                                indicator = ' ✓ (Correct answer)';
                            } else if (isUserAnswer) {
                                optionClass = 'text-danger fw-bold';
                                indicator = ' ✗ (Your answer)';
                            }
                            
                            detailsHTML += `<li class="${optionClass}">${option}${indicator}</li>`;
                        });
                        detailsHTML += '</ul>';
                    }
                    
                    // Specific correct answer if not in options
                    if (question.correct_answer && (!question.options || !question.options.includes(question.correct_answer))) {
                        detailsHTML += `<p><strong>Correct Answer:</strong> <span class="text-success">${question.correct_answer}</span></p>`;
                    }
                    
                    // User answer if not in options
                    if (question.user_answer && question.user_answer !== "No Response" && 
                        (!question.options || !question.options.includes(question.user_answer))) {
                        const answerClass = question.is_correct ? 'text-success' : 'text-danger';
                        detailsHTML += `<p><strong>Your Answer:</strong> <span class="${answerClass}">${question.user_answer}</span></p>`;
                    }
                }
                
                // Feedback
                if (question.feedback) {
                    detailsHTML += `<p><strong>Feedback:</strong> ${question.feedback}</p>`;
                }
                
                // Images
                if (question.image_urls && question.image_urls.length > 0) {
                    detailsHTML += '<p><strong>Images:</strong></p>';
                    question.image_urls.forEach(url => {
                        detailsHTML += `<p><a href="${url}" target="_blank" class="btn btn-sm btn-outline-primary">View Image</a></p>`;
                    });
                }
                
                body.innerHTML = detailsHTML;
                collapseDiv.appendChild(body);
                accordionItem.appendChild(collapseDiv);
                
                accordion.appendChild(accordionItem);
            });
            
            document.getElementById('results').classList.remove('hidden');
        }
    </script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
        ''')
    
    app.run(debug=True, host='0.0.0.0', port=5000)