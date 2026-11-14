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
        'say': "Your turn! Look at this equation: y equals three x minus four. What is the slope, and where "
               "does the line cross the y axis? Take a few seconds and think...",
        'board': [
            {'type': 'icon', 'name': 'question', 'label': 'Your turn', 'color': 'pink', 'at': 0},
            {'type': 'math', 'tex': 'y = 3x - 4', 'at': 0.3},
            {'type': 'text', 'text': 'm = ?   and   b = ?', 'color': 'pink', 'at': 0.6},
        ],
        'think': 5,
    },
    {
        'say': "The slope is three: the number in front of x. And b is minus four, not four! Students drop that "
               "minus sign all the time, so keep the sign with the number. Did you get both? Brilliant!",
        'board': [
            {'type': 'text', 'text': '**m = 3**', 'color': 'sky', 'at': 0},
            {'type': 'text', 'text': '**b = -4**', 'color': 'green', 'at': 0.25},
            {'type': 'fix', 'wrong': 'b = 4', 'right': 'b = -4', 'at': 0.45},
            {'type': 'note', 'text': 'The sign belongs to the number.', 'at': 0.8},
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
        'say': "Now, two exam secrets most students never notice. First: if a line goes down from left to right, "
               "its slope is negative, so you can cross out every positive answer before you calculate anything. "
               "Second: in word problems, the number next to per, each or every is almost always the slope.",
        'board': [
            {'type': 'heading', 'text': 'Exam secrets', 'at': 0},
            {'type': 'secret', 'text': 'Line goes **down**: the slope is **negative**. Cross out the positive answers first.',
             'at': 0.1},
            {'type': 'secret', 'text': '**per**, **each**, **every**: that number is \\(m\\).', 'at': 0.6},
        ],
    },
    {
        'say': "Let's sum up. m is the rate of change, b is the starting value. On a graph, pick two points, "
               "find the slope, then read where the line crosses the y axis. In a word problem, separate what "
               "repeats from what you pay once. Now, a quick final test to lock it all in. You've got this!",
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

# after_step is the index in STEPS; is_quiz puts it in the final test, is_homework in the homework.
QUESTIONS = [
    {
        'after_step': 4,
        'kind': 'CHOICE',
        'prompt': 'A line passes through \\((1, 3)\\) and \\((3, 7)\\). What is its slope?',
        'options': ['1', '2', '4', '\\(\\frac{1}{2}\\)'],
        'correct': 1,
        'explanation': '\\(m = \\frac{7 - 3}{3 - 1} = \\frac{4}{2} = 2\\)',
    },
    {
        'after_step': 6,
        'kind': 'WRITTEN',
        'prompt': 'A line crosses the \\(y\\)-axis at 3 and goes up 2 for every 1 step to the right. '
                  'Write its equation.',
        'rubric': 'The equation y = 2x + 3 (or the same line written another way) must be there.',
        'explanation': 'Up 2 for every 1 to the right: \\(m = 2\\). It crosses at 3: \\(b = 3\\). So \\(y = 2x + 3\\).',
    },
    {
        'is_quiz': True,
        'kind': 'TRUE_FALSE',
        'prompt': 'The line \\(y = -2x + 5\\) goes down from left to right.',
        'correct': True,
        'explanation': 'The slope is \\(-2\\), a negative number, so the line goes down.',
    },
    {
        'is_quiz': True,
        'kind': 'GAP',
        'prompt': 'In \\(y = 4x - 7\\), the slope is ___.',
        'accepted': ['4'],
        'explanation': 'The slope is the number in front of \\(x\\): 4. The \\(-7\\) is \\(b\\).',
    },
    {
        'is_quiz': True,
        'kind': 'GAP',
        'prompt': 'A line passes through \\((0, 1)\\) and \\((2, 7)\\). Its slope is ___.',
        'accepted': ['3'],
        'explanation': '\\(m = \\frac{7 - 1}{2 - 0} = \\frac{6}{2} = 3\\)',
    },
    {
        'is_quiz': True,
        'kind': 'MISTAKE',
        'prompt': 'Find the mistake in this solution.',
        'options': [
            'Points: \\((1, 2)\\) and \\((3, 8)\\)',
            '\\(m = \\frac{8 - 2}{1 - 3}\\)',
            'so \\(m = -3\\)',
        ],
        'correct': 1,
        'explanation': 'The order flipped at the bottom. It must be \\(\\frac{8 - 2}{3 - 1} = \\frac{6}{2} = 3\\).',
    },
    {
        'is_quiz': True,
        'kind': 'CHOICE',
        'prompt': 'A phone plan costs **10 dollars** a month plus **0.05 dollars** per text. '
                  'Which equation gives the monthly cost \\(y\\) for \\(x\\) texts?',
        'options': ['\\(y = 10x + 0.05\\)', '\\(y = 0.05x + 10\\)', '\\(y = 10.05x\\)', '\\(y = 0.05x - 10\\)'],
        'correct': 1,
        'explanation': '"Per text" repeats, so \\(m = 0.05\\); the monthly fee is paid once, so \\(b = 10\\).',
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
