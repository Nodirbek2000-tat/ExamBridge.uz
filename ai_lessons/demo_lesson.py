"""
A hand-written lesson for trying the board player without spending any tokens.
Loaded by `manage.py seed_demo_ai_lesson`.
"""

TITLE = "SAT Math: to'g'ri chiziq tenglamasi y = mx + b"

LESSON = {
    'subject': 'SAT',
    'section': 'math',
    'language': 'uz',
    'duration_minutes': 10,
    'level': 'SAT 1000+',
    'brief': "Demo dars: qiyalik, y o'qini kesish nuqtasi, grafik va matnli masala.",
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
        {'kind': 'line', 'from': [30, 260], 'to': [150, 20], 'color': 'yellow'},
        {'kind': 'circle', 'center': [60, 200], 'r': 6, 'filled': True, 'color': 'sky'},
        {'kind': 'label', 'pos': [72, 216], 'text': '(0, 2)', 'color': 'sky'},
        {'kind': 'circle', 'center': [120, 80], 'r': 6, 'filled': True, 'color': 'sky'},
        {'kind': 'label', 'pos': [132, 76], 'text': '(2, 6)', 'color': 'sky'},
        {'kind': 'line', 'from': [60, 200], 'to': [120, 200], 'dashed': True, 'color': 'pink'},
        {'kind': 'label', 'pos': [74, 192], 'text': '+2', 'color': 'pink'},
        {'kind': 'line', 'from': [120, 200], 'to': [120, 80], 'dashed': True, 'color': 'pink'},
        {'kind': 'label', 'pos': [128, 146], 'text': '+4', 'color': 'pink'},
    ],
}

STEPS = [
    {
        'say': "Salom! Bugun SAT Math bo'limida eng ko'p uchraydigan mavzulardan biri bilan ishlaymiz: "
               "to'g'ri chiziq tenglamasi. Dars oxirida grafikka qarab tenglama yozishni va matnli "
               "masalani tenglamaga aylantirishni o'rganasiz.",
        'board': [
            {'type': 'heading', 'text': "To'g'ri chiziq tenglamasi", 'at': 0},
            {'type': 'text', 'text': 'Bugun nimani o\'rganamiz:', 'color': 'yellow', 'at': 0.3},
            {'type': 'list', 'at': 0.45, 'items': [
                'Tenglamadagi \\(m\\) va \\(b\\) nimani bildiradi',
                'Grafikdan tenglama yozish',
                'Matnli masalani yechish',
            ]},
        ],
    },
    {
        'say': "Har qanday to'g'ri chiziqni shunday yozish mumkin: y teng m x plyus b. Bu yerda ikkita "
               "muhim son bor. m chiziqning qiyaligi, ya'ni x bittaga oshganda y qanchaga o'zgarishini "
               "ko'rsatadi. b esa chiziq y o'qini kesib o'tadigan nuqta.",
        'board': [
            {'type': 'math', 'tex': 'y = mx + b', 'at': 0.05},
            {'type': 'text', 'text': "**m** — qiyalik: \\(x\\) 1 ga oshganda \\(y\\) qanchaga o'zgaradi",
             'at': 0.4},
            {'type': 'text', 'text': "**b** — chiziq \\(y\\) o'qini kesib o'tadigan nuqta", 'at': 0.75},
        ],
    },
    {
        'say': "Ikki nuqta berilgan bo'lsa, qiyalikni shu formula bilan topamiz: y lar farqini x lar "
               "farqiga bo'lamiz. Eslab qoling, ikkala farqda ham nuqtalar tartibi bir xil bo'lishi kerak.",
        'board': [
            {'type': 'heading', 'text': 'Qiyalikni topish', 'at': 0},
            {'type': 'math', 'tex': 'm = \\frac{y_2 - y_1}{x_2 - x_1}', 'at': 0.25},
            {'type': 'note', 'text': "Tartib bir xil bo'lsin: ikkala farqda ham avval 2-nuqta, keyin 1-nuqta.",
             'at': 0.65},
        ],
    },
    {
        'say': "Keling, grafikka qaraymiz. Chiziq ikkita nuqtadan o'tadi: nol va ikki, hamda ikki va olti. "
               "Birinchi nuqtadan ikkinchisiga borish uchun o'ngga ikki birlik, tepaga to'rt birlik yuramiz.",
        'board': [
            {**GRAPH, 'at': 0},
        ],
    },
    {
        'say': "Endi hisoblaymiz. Qiyalik to'rtni ikkiga bo'lgani, ya'ni ikki. Chiziq y o'qini ikkida kesadi, "
               "demak b ikkiga teng. Javob: y teng ikki x plyus ikki.",
        'board': [
            {'type': 'math', 'tex': 'm = \\frac{6 - 2}{2 - 0} = \\frac{4}{2} = 2', 'at': 0.05},
            {'type': 'math', 'tex': 'b = 2', 'at': 0.45},
            {'type': 'note', 'text': '**Javob:** \\(y = 2x + 2\\)', 'at': 0.72},
        ],
    },
    {
        'say': "SAT da bu mavzu ko'pincha matnli masala ko'rinishida keladi. Masalan: taksiga o'tirish besh "
               "dollar, har bir kilometr uchun ikki dollar to'lanadi. Sakkiz kilometrlik yo'l qancha turadi?",
        'board': [
            {'type': 'heading', 'text': 'Matnli masala', 'at': 0},
            {'type': 'text', 'text': "Taksiga o'tirish **5 dollar**, har bir km uchun **2 dollar**.", 'at': 0.3},
            {'type': 'text', 'text': "8 km yo'l necha dollar turadi?", 'color': 'yellow', 'at': 0.75},
        ],
    },
    {
        'say': "Har safar takrorlanadigan qism, ya'ni kilometr narxi, bu m. Bir marta to'lanadigan boshlang'ich "
               "summa esa b. Demak tenglama y teng ikki x plyus besh. Endi x o'rniga sakkizni qo'yamiz: "
               "o'n olti plyus besh, yigirma bir dollar.",
        'board': [
            {'type': 'text', 'text': 'Takrorlanadigan narx: **m = 2**', 'at': 0},
            {'type': 'text', 'text': "Boshlang'ich summa: **b = 5**", 'at': 0.22},
            {'type': 'math', 'tex': 'y = 2x + 5', 'at': 0.45},
            {'type': 'math', 'tex': 'y = 2 \\cdot 8 + 5 = 21', 'at': 0.68},
            {'type': 'note', 'text': '**Javob:** 21 dollar', 'at': 0.9},
        ],
    },
    {
        'say': "Xulosa qilamiz. m bu o'zgarish tezligi, b bu boshlang'ich qiymat. Grafikda ikki nuqtani tanlang, "
               "qiyalikni toping, keyin y o'qini kesgan joyni o'qing. Matnli masalada esa takrorlanadigan va "
               "bir martalik summani ajrating.",
        'board': [
            {'type': 'heading', 'text': 'Xulosa', 'at': 0},
            {'type': 'list', 'at': 0.2, 'items': [
                "\\(m\\) — o'zgarish tezligi",
                "\\(b\\) — boshlang'ich qiymat",
                "Grafikda: ikki nuqta, qiyalik, keyin \\(y\\) o'qi",
                'Masalada: takrorlanadigan va bir martalik summa',
            ]},
        ],
    },
]

# after_step is the index in STEPS; without it the question is homework.
QUESTIONS = [
    {
        'after_step': 2,
        'kind': 'CHOICE',
        'prompt': 'Chiziq \\((1, 3)\\) va \\((3, 7)\\) nuqtalardan o\'tadi. Uning qiyaligi nechaga teng?',
        'options': ['1', '2', '4', '\\(\\frac{1}{2}\\)'],
        'correct': 1,
        'explanation': '\\(m = \\frac{7 - 3}{3 - 1} = \\frac{4}{2} = 2\\)',
    },
    {
        'after_step': 4,
        'kind': 'WRITTEN',
        'prompt': '\\(y = 3x - 1\\) tenglamasida \\(m\\) va \\(b\\) nimaga teng?',
        'rubric': 'm = 3 va b = -1 ikkalasi ham to\'g\'ri bo\'lishi kerak.',
        'explanation': '\\(x\\) oldidagi son \\(m = 3\\), alohida turgan son esa \\(b = -1\\).',
    },
    {
        'is_homework': True,
        'kind': 'CHOICE',
        'prompt': 'Chiziq \\((0, -2)\\) va \\((4, 6)\\) nuqtalardan o\'tadi. Uning tenglamasi qaysi?',
        'options': ['\\(y = 2x - 2\\)', '\\(y = 2x + 2\\)', '\\(y = -2x - 2\\)', '\\(y = \\frac{1}{2}x - 2\\)'],
        'correct': 0,
        'explanation': '\\(m = \\frac{6 - (-2)}{4 - 0} = 2\\), chiziq \\(y\\) o\'qini \\(-2\\) da kesadi.',
    },
    {
        'is_homework': True,
        'kind': 'CHOICE',
        'prompt': "Sport zaliga a'zolik **20 dollar**, har bir mashg'ulot **4 dollar**. "
                  "15 ta mashg'ulot jami necha dollar turadi?",
        'options': ['60', '75', '80', '95'],
        'correct': 2,
        'explanation': '\\(y = 4x + 20\\), demak \\(y = 4 \\cdot 15 + 20 = 80\\).',
    },
    {
        'is_homework': True,
        'kind': 'WRITTEN',
        'prompt': 'Balansda **45 dollar** bor va u har kuni **3 dollarga** kamayadi. '
                  'Tenglamani yozing va pul necha kunda tugashini toping.',
        'rubric': 'Tenglama y = -3x + 45 va javob 15 kun bo\'lishi kerak.',
        'explanation': '\\(y = -3x + 45\\). \\(y = 0\\) bo\'lganda \\(3x = 45\\), ya\'ni \\(x = 15\\) kun.',
    },
]
