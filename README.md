# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                   |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django/case\_prep/forms.py                                             |        7 |        7 |      0% |      1-10 |
| django/case\_prep/models.py                                            |       34 |        2 |     94% |    23, 48 |
| django/case\_prep/views.py                                             |      194 |      118 |     39% |62, 86-101, 113, 135, 158-170, 174-207, 213-262, 266-278, 283-291, 295-306, 310-372, 377-402 |
| django/chat/forms.py                                                   |      125 |       23 |     82% |34, 42, 76, 96-108, 112-117, 126, 141, 145-150, 159, 306, 308-310 |
| django/chat/llm.py                                                     |       85 |       14 |     84% |41, 60-62, 68-70, 88-94, 214, 238 |
| django/chat/metrics/activity\_metrics.py                               |        4 |        0 |    100% |           |
| django/chat/metrics/feedback\_metrics.py                               |        3 |        0 |    100% |           |
| django/chat/models.py                                                  |      176 |       30 |     83% |72, 75-76, 91-95, 210-213, 218-224, 268, 271, 308-309, 313-315, 319, 357, 361-373 |
| django/chat/prompts.py                                                 |        6 |        0 |    100% |           |
| django/chat/responses.py                                               |      172 |       65 |     62% |52, 56, 88, 150-171, 185, 231-298, 320-333, 336-343, 367, 392, 414, 423-425, 441-442 |
| django/chat/tasks.py                                                   |       59 |       42 |     29% |22-30, 35-98 |
| django/chat/utils.py                                                   |      158 |       37 |     77% |38, 116-143, 183, 197, 232-244, 273-276, 291-293, 308, 343, 345 |
| django/chat/views.py                                                   |      350 |       66 |     81% |84-86, 100-101, 127-128, 156-157, 172-173, 257-259, 296-297, 299, 301, 314, 336, 356-363, 367-368, 520-564, 595-600, 647, 652, 674, 700, 715-716, 772-780, 789-790 |
| django/import\_timer.py                                                |        6 |        6 |      0% |       1-8 |
| django/laws/forms.py                                                   |       54 |        6 |     89% |24-29, 38, 52-57, 66 |
| django/laws/management/commands/load\_laws\_xml.py                     |      434 |      120 |     72% |26, 30-59, 74, 85-87, 103-104, 114-118, 146, 175, 236, 254, 256, 258, 277, 280, 282, 297-298, 300-301, 398-401, 411-429, 455-459, 471, 497, 549-550, 591-593, 704-708, 726-727, 729, 737, 777, 779, 797-799, 829-831, 834-836, 844-846, 848-850, 852-854, 856-858, 905-907, 923-925, 943-949, 997-1008, 1013, 1022-1023, 1046-1052 |
| django/laws/models.py                                                  |      104 |       22 |     79% |42-46, 90, 115-118, 152, 156-164, 168-169 |
| django/laws/prompts.py                                                 |        2 |        0 |    100% |           |
| django/laws/tests.py                                                   |        1 |        0 |    100% |           |
| django/laws/translation.py                                             |        5 |        0 |    100% |           |
| django/laws/utils.py                                                   |       94 |       77 |     18% |25-35, 40-46, 50-65, 69-85, 92-105, 109-159 |
| django/laws/views.py                                                   |      201 |       53 |     74% |57-87, 112, 118, 128-149, 159, 194, 216, 253, 255, 260-262, 271, 274, 278, 304, 312, 320, 336-354, 391, 412-420 |
| django/librarian/forms.py                                              |       85 |       30 |     65% |77-82, 104-111, 186-197, 203-212 |
| django/librarian/metrics/activity\_metrics.py                          |        9 |        9 |      0% |      1-50 |
| django/librarian/models.py                                             |      259 |       78 |     70% |46-48, 109, 120, 127-128, 135-136, 142-144, 162, 166, 204, 257-259, 262-263, 329, 333, 337, 341-350, 354, 360, 366-371, 375, 379-382, 385-386, 389-394, 397-409, 412-419, 422, 438, 441-445 |
| django/librarian/tasks.py                                              |       85 |       85 |      0% |     1-152 |
| django/librarian/translation.py                                        |        8 |        0 |    100% |           |
| django/librarian/views.py                                              |      241 |      139 |     42% |64-109, 115-159, 170-188, 192-195, 214-230, 243-252, 284-293, 308, 315-317, 323, 328, 335, 342, 349, 354, 359, 366, 390-395, 401-403, 414-425, 432-439 |
| django/otto/celery.py                                                  |       16 |        1 |     94% |        35 |
| django/otto/context\_processors.py                                     |        3 |        0 |    100% |           |
| django/otto/forms.py                                                   |       48 |        6 |     88% |   131-140 |
| django/otto/management/commands/reset\_app\_data.py                    |      124 |       20 |     84% |67-72, 90, 104-109, 129-134, 155-160, 174-175, 180-183, 198-203, 214 |
| django/otto/metrics/activity\_metrics.py                               |        2 |        0 |    100% |           |
| django/otto/metrics/feedback\_metrics.py                               |        3 |        0 |    100% |           |
| django/otto/models.py                                                  |      236 |       33 |     86% |26-28, 63, 67-70, 89, 93-96, 131, 173, 189, 210, 217, 235, 296, 299, 335, 337, 347, 353, 357, 361, 365, 369, 378, 425-426, 440, 444, 448 |
| django/otto/rules.py                                                   |      120 |       22 |     82% |23, 40, 49, 87, 119, 147-151, 157, 162-166, 171, 176, 182, 186-187, 192 |
| django/otto/secure\_models.py                                          |      248 |       63 |     75% |21-22, 61, 86-100, 129-130, 135-136, 149-154, 183-224, 248, 268-269, 307, 337, 350, 359, 378, 393, 398, 403, 409-415, 418, 423, 437, 442, 447, 491-498, 517, 536-537, 549-552 |
| django/otto/settings.py                                                |      145 |       22 |     85% |37-39, 49-50, 202-211, 276-277, 354-360, 381, 408, 465-466 |
| django/otto/tasks.py                                                   |        8 |        8 |      0% |      1-13 |
| django/otto/templatetags/filters.py                                    |       10 |        0 |    100% |           |
| django/otto/templatetags/tags.py                                       |       10 |        1 |     90% |        18 |
| django/otto/translation.py                                             |       17 |        0 |    100% |           |
| django/otto/utils/auth.py                                              |       34 |        6 |     82% |     15-29 |
| django/otto/utils/cache.py                                             |       91 |       39 |     57% |25-30, 44, 55-60, 63-72, 75-80, 87-94, 110-112 |
| django/otto/utils/common.py                                            |       19 |        5 |     74% |22, 29-30, 37-38 |
| django/otto/utils/decorators.py                                        |       46 |        4 |     91% |22-23, 62, 84 |
| django/otto/utils/logging.py                                           |       15 |        0 |    100% |           |
| django/otto/views.py                                                   |      310 |      154 |     50% |40, 45-59, 100, 110-121, 169, 224, 270-273, 277-281, 291, 294-297, 303-304, 334-351, 356-368, 373-435, 450-655 |
| django/template\_wizard/metrics/template\_wizard\_activity\_metrics.py |        2 |        0 |    100% |           |
| django/template\_wizard/models.py                                      |        9 |        0 |    100% |           |
| django/template\_wizard/translation.py                                 |        0 |        0 |    100% |           |
| django/template\_wizard/views.py                                       |       69 |       17 |     75% |63-70, 96, 146-153, 165-200 |
| django/template\_wizard/wizards/canlii\_wizard/utils.py                |      402 |      360 |     10% |82-144, 149-164, 169-177, 181-232, 236-248, 253-270, 275-291, 295-300, 304-391, 396-657, 662-971, 976-1197 |
| django/template\_wizard/wizards/canlii\_wizard/views.py                |      126 |      100 |     21% |49, 53-98, 111-116, 130-154, 159-211, 223-251, 256-289, 294-302 |
| django/text\_extractor/models.py                                       |       14 |        2 |     86% |    12, 24 |
| django/text\_extractor/tests.py                                        |        1 |        0 |    100% |           |
| django/text\_extractor/utils.py                                        |      149 |       84 |     44% |48-71, 112-113, 131-287 |
| django/text\_extractor/views.py                                        |      104 |       86 |     17% |29-32, 37-207, 211-226 |
|                                                              **TOTAL** | **5342** | **2062** | **61%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/justicecanada/otto/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/justicecanada/otto/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fjusticecanada%2Fotto%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.