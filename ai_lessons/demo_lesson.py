"""
A hand-written lesson for trying the board player without spending any tokens.
Loaded by `manage.py seed_demo_ai_lesson`.
"""

TITLE = 'SAT Math: the line equation y = mx + b'

LESSON = {
    'subject': 'SAT',
    'section': 'math',
    'language': 'en',
    'duration_minutes': 10,
    'level': 'SAT 1000+',
    'voice': 'coral',
    'brief': 'Demo lesson: slope, y-intercept, reading a graph and a word problem.',
}

# Graph of y = 2x + 2, one unit is 30 points, the origin sits at (60, 260).
GRAPH = {
    'type': 'diagram',
    'width': 400,
    'height': 300,
    'shapes': [
        {'kind': 'line', 'from': [20, 260], 'to': [380, 260], 'arrow': True},
        {'kind': 'line', 'from': [60, 295], 'to': [60, 10], 'arrow': True},
        {'kind': 'label', 'pos': [370, 285], 'text': 'x'},
        {'kind': 'label', 'pos': [38, 22], 'text': 'y'},
        {'kind': 'line', 'from': [30, 260], 'to': [150, 20], 'color': 'sky'},
        {'kind': 'circle', 'center': [60, 200], 'r': 6, 'filled': True, 'color': 'green'},
        {'kind': 'label', 'pos': [72, 216], 'text': '(0, 2)', 'color': 'green'},
        {'kind': 'circle', 'center': [120, 80], 'r': 6, 'filled': True, 'color': 'green'},
        {'kind': 'label', 'pos': [132, 76], 'text': '(2, 6)', 'color': 'green'},
        {'kind': 'line', 'from': [60, 200], 'to': [120, 200], 'dashed': True, 'color': 'pink'},
        {'kind': 'label', 'pos': [74, 192], 'text': '+2', 'color': 'pink'},
        {'kind': 'line', 'from': [120, 200], 'to': [120, 80], 'dashed': True, 'color': 'pink'},
        {'kind': 'label', 'pos': [128, 146], 'text': '+4', 'color': 'pink'},
    ],
}

STEPS = [
    {
        'say': "Hi everyone! Today we're tackling one of the most common topics in SAT Math: the equation "
               "of a line. By the end, you'll write an equation straight from a graph and turn a word "
               "problem into an equation. Ready? Let's go!",
        'board': [
            {'type': 'heading', 'text': 'The equation of a line', 'at': 0},
            {'type': 'icon', 'name': 'chart-line', 'label': 'Lines', 'color': 'sky', 'at': 0.2},
            {'type': 'text', 'text': "Today's plan:", 'color': 'yellow', 'at': 0.4},
            {'type': 'list', 'at': 0.5, 'items': [
                'What \\(m\\) and \\(b\\) mean',
                'From a graph to an equation',
                'Word problems',
            ]},
        ],
    },
    {
        'say': "Every straight line can be written like this: y equals m x plus b. Two numbers matter here. "
               "m is the slope: how much y changes when x goes up by one. And b is where the line crosses "
               "the y axis. Got it? Great.",
        'board': [
            {'type': 'math', 'tex': 'y = mx + b', 'at': 0.05},
            {'type': 'text', 'text': '**m** = slope: how much \\(y\\) changes when \\(x\\) goes up by 1',
             'at': 0.4},
            {'type': 'text', 'text': '**b** = where the line crosses the \\(y\\)-axis', 'color': 'green', 'at': 0.75},
        ],
    },
    {
        'say': "Given two points, we find the slope with this formula: the difference in y divided by the "
               "difference in x. Remember: keep the same order of points on top and on the bottom.",
        'board': [
            {'type': 'heading', 'text': 'Finding the slope', 'at': 0},
            {'type': 'math', 'tex': 'm = \\frac{y_2 - y_1}{x_2 - x_1}', 'at': 0.25},
            {'type': 'note', 'text': 'Same order on top and bottom: point 2 first, then point 1.', 'at': 0.65},
        ],
    },
    {
        'say': "Now let's look at a graph. The line passes through two points: zero comma two, and two comma six. "
               "To get from the first point to the second, we go two units right and four units up.",
        'board': [
            {**GRAPH, 'at': 0},
        ],
    },
    {
        'say': "Let's calculate. The slope is four divided by two, so two. The line crosses the y axis at two, "
               "so b is two. The answer: y equals two x plus two. Nice work!",
        'board': [
            {'type': 'math', 'tex': 'm = \\frac{6 - 2}{2 - 0} = \\frac{4}{2} = 2', 'at': 0.05},
            {'type': 'math', 'tex': 'b = 2', 'at': 0.45},
            {'type': 'note', 'text': '**Answer:** \\(y = 2x + 2\\)', 'at': 0.72},
        ],
    },
    {
        'say': "On the SAT, this topic often comes as a word problem. For example: a taxi ride costs five dollars "
               "to start, plus two dollars for every kilometer. How much is an eight kilometer ride?",
        'board': [
            {'type': 'heading', 'text': 'Word problem', 'at': 0},
            {'type': 'icon', 'name': 'car', 'label': 'Taxi', 'color': 'yellow', 'at': 0.15},
            {'type': 'text', 'text': 'Start: **5 dollars**, then **2 dollars** per km.', 'at': 0.35},
            {'type': 'text', 'text': 'How much is an 8 km ride?', 'color': 'pink', 'at': 0.75},
        ],
    },
    {
        'say': "The part that repeats, the price per kilometer, is m. The one-time starting fee is b. So the "
               "equation is y equals two x plus five. Now put eight in for x: sixteen plus five, twenty-one dollars.",
        'board': [
            {'type': 'text', 'text': 'Repeats every km: **m = 2**', 'at': 0},
            {'type': 'text', 'text': 'One-time fee: **b = 5**', 'at': 0.22},
            {'type': 'math', 'tex': 'y = 2x + 5', 'at': 0.45},
            {'type': 'math', 'tex': 'y = 2 \\cdot 8 + 5 = 21', 'at': 0.68},
            {'type': 'note', 'text': '**Answer:** 21 dollars', 'at': 0.9},
        ],
    },
    {
        'say': "Let's sum up. m is the rate of change, b is the starting value. On a graph, pick two points, "
               "find the slope, then read where the line crosses the y axis. In a word problem, separate what "
               "repeats from what you pay once. You've got this!",
        'board': [
            {'type': 'heading', 'text': 'Summary', 'at': 0},
            {'type': 'icon', 'name': 'trophy', 'label': 'You got it!', 'color': 'yellow', 'at': 0.12},
            {'type': 'list', 'at': 0.3, 'items': [
                '\\(m\\) = rate of change',
                '\\(b\\) = starting value',
                'Graph: two points, slope, then the \\(y\\)-axis',
                'Word problem: what repeats vs. what you pay once',
            ]},
        ],
    },
]

# after_step is the index in STEPS; without it the question is homework.
QUESTIONS = [
    {
        'after_step': 2,
        'kind': 'CHOICE',
        'prompt': 'A line passes through \\((1, 3)\\) and \\((3, 7)\\). What is its slope?',
        'options': ['1', '2', '4', '\\(\\frac{1}{2}\\)'],
        'correct': 1,
        'explanation': '\\(m = \\frac{7 - 3}{3 - 1} = \\frac{4}{2} = 2\\)',
    },
    {
        'after_step': 4,
        'kind': 'WRITTEN',
        'prompt': 'In \\(y = 3x - 1\\), what are \\(m\\) and \\(b\\)?',
        'rubric': 'Both m = 3 and b = -1 must be correct.',
        'explanation': 'The number in front of \\(x\\) is \\(m = 3\\); the number on its own is \\(b = -1\\).',
    },
    {
        'is_homework': True,
        'kind': 'CHOICE',
        'prompt': 'A line passes through \\((0, -2)\\) and \\((4, 6)\\). Which is its equation?',
        'options': ['\\(y = 2x - 2\\)', '\\(y = 2x + 2\\)', '\\(y = -2x - 2\\)', '\\(y = \\frac{1}{2}x - 2\\)'],
        'correct': 0,
        'explanation': '\\(m = \\frac{6 - (-2)}{4 - 0} = 2\\) and the line crosses the \\(y\\)-axis at \\(-2\\).',
    },
    {
        'is_homework': True,
        'kind': 'CHOICE',
        'prompt': 'A gym membership costs **20 dollars**, plus **4 dollars** per class. '
                  'How much are 15 classes in total?',
        'options': ['60', '75', '80', '95'],
        'correct': 2,
        'explanation': '\\(y = 4x + 20\\), so \\(y = 4 \\cdot 15 + 20 = 80\\).',
    },
    {
        'is_homework': True,
        'kind': 'WRITTEN',
        'prompt': 'An account has **45 dollars** and loses **3 dollars** every day. '
                  'Write the equation and find after how many days the money runs out.',
        'rubric': 'The equation y = -3x + 45 and the answer 15 days must both be there.',
        'explanation': '\\(y = -3x + 45\\). When \\(y = 0\\), \\(3x = 45\\), so \\(x = 15\\) days.',
    },
]
