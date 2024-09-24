# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                   |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django/case\_prep/forms.py                                             |        7 |        7 |      0% |      1-10 |
| django/case\_prep/models.py                                            |       34 |        2 |     94% |    23, 48 |
| django/case\_prep/views.py                                             |      194 |      118 |     39% |62, 86-101, 113, 135, 158-170, 174-207, 213-262, 266-278, 283-291, 295-306, 310-372, 377-402 |
| django/chat/forms.py                                                   |      125 |       23 |     82% |34, 42, 76, 96-108, 112-117, 126, 141, 145-150, 159, 313, 315-317 |
| django/chat/llm.py                                                     |       85 |       14 |     84% |41, 60-62, 68-70, 88-94, 214, 238 |
| django/chat/metrics/activity\_metrics.py                               |        4 |        0 |    100% |           |
| django/chat/metrics/feedback\_metrics.py                               |        3 |        0 |    100% |           |
| django/chat/models.py                                                  |      182 |       30 |     84% |30, 85, 88-89, 104-108, 224-227, 232-238, 282, 322-323, 327-329, 333, 371, 375-387 |
| django/chat/prompts.py                                                 |        9 |        0 |    100% |           |
| django/chat/responses.py                                               |      209 |       66 |     68% |56, 60, 96, 154-175, 189, 235-302, 324-337, 340-347, 395, 428-430, 447-448, 466, 511-512 |
| django/chat/tasks.py                                                   |       59 |       42 |     29% |22-30, 35-98 |
| django/chat/utils.py                                                   |      169 |       43 |     75% |38, 116-143, 184, 199, 201-202, 214-225, 252-264, 293-296, 311-313, 328, 363, 365 |
| django/chat/views.py                                                   |      351 |       66 |     81% |84-86, 100-101, 127-128, 156-157, 172-173, 257-259, 296-297, 299-300, 309, 331, 351-358, 362-363, 518-562, 593-598, 645, 650, 672, 698, 713-714, 770-778, 787-788 |
| django/import\_timer.py                                                |        6 |        6 |      0% |       1-8 |
| django/laws/forms.py                                                   |       54 |        6 |     89% |24-29, 38, 52-57, 66 |
| django/laws/management/commands/load\_laws\_xml.py                     |      434 |      120 |     72% |26, 30-59, 74, 85-87, 103-104, 114-118, 146, 175, 236, 254, 256, 258, 277, 280, 282, 297-298, 300-301, 398-401, 411-429, 455-459, 471, 497, 549-550, 591-593, 704-708, 726-727, 729, 737, 777, 779, 797-799, 829-831, 834-836, 844-846, 848-850, 852-854, 856-858, 905-907, 923-925, 943-949, 997-1008, 1013, 1022-1023, 1046-1052 |
| django/laws/models.py                                                  |      104 |       22 |     79% |42-46, 90, 115-118, 152, 156-164, 168-169 |
| django/laws/prompts.py                                                 |        2 |        0 |    100% |           |
| django/laws/tests.py                                                   |        1 |        0 |    100% |           |
| django/laws/translation.py                                             |        5 |        0 |    100% |           |
| django/laws/utils.py                                                   |      103 |       87 |     16% |16-18, 26-36, 41-47, 51-66, 70-86, 93-106, 110-165, 173-174 |
| django/laws/views.py                                                   |      208 |       96 |     54% |60-90, 95-188, 197-208, 215, 237, 274, 276, 281-283, 292, 295, 299, 325, 333, 341, 357-375, 417-425 |
| django/librarian/forms.py                                              |       85 |       30 |     65% |77-82, 104-111, 186-197, 203-212 |
| django/librarian/metrics/activity\_metrics.py                          |        9 |        9 |      0% |      1-50 |
| django/librarian/models.py                                             |      276 |       82 |     70% |46-48, 116, 118, 126, 128, 130, 136, 145-146, 153-154, 160-162, 180, 184, 222, 266-268, 271-272, 338, 342, 346, 350-359, 363, 369, 375-380, 384, 388-391, 394-395, 398-403, 406-418, 421-428, 431, 447, 450-454 |
| django/librarian/tasks.py                                              |       85 |       85 |      0% |     1-152 |
| django/librarian/translation.py                                        |        8 |        0 |    100% |           |
| django/librarian/views.py                                              |      241 |      139 |     42% |64-109, 115-159, 170-188, 192-195, 214-230, 243-252, 284-293, 308, 315-317, 323, 328, 335, 342, 349, 354, 359, 366, 390-395, 401-403, 414-425, 432-439 |
| django/otto/celery.py                                                  |       16 |        1 |     94% |        35 |
| django/otto/context\_processors.py                                     |        3 |        0 |    100% |           |
| django/otto/forms.py                                                   |       48 |        6 |     88% |   131-140 |
| django/otto/management/commands/reset\_app\_data.py                    |      124 |       20 |     84% |67-72, 90, 104-109, 129-134, 155-160, 174-175, 180-183, 198-203, 214 |
| django/otto/metrics/activity\_metrics.py                               |        2 |        0 |    100% |           |
| django/otto/metrics/feedback\_metrics.py                               |        3 |        0 |    100% |           |
| django/otto/models.py                                                  |      236 |       32 |     86% |26-28, 63, 67-70, 89, 93-96, 131, 173, 189, 210, 217, 235, 296, 299, 335, 347, 353, 357, 361, 365, 369, 378, 426-427, 441, 445, 449 |
| django/otto/rules.py                                                   |      121 |       22 |     82% |24, 41, 50, 88, 120, 148-152, 158, 163-167, 172, 177, 183, 187-188, 193 |
| django/otto/secure\_models.py                                          |      248 |       63 |     75% |21-22, 61, 86-100, 129-130, 135-136, 149-154, 183-224, 248, 268-269, 307, 337, 350, 359, 378, 393, 398, 403, 409-415, 418, 423, 437, 442, 447, 491-498, 517, 536-537, 549-552 |
| django/otto/settings.py                                                |      145 |       22 |     85% |37-39, 49-50, 202-211, 276-277, 354-360, 381, 408, 465-466 |
| django/otto/tasks.py                                                   |        8 |        8 |      0% |      1-13 |
| django/otto/templatetags/filters.py                                    |       10 |        0 |    100% |           |
| django/otto/templatetags/tags.py                                       |       10 |        1 |     90% |        18 |
| django/otto/translation.py                                             |       17 |        0 |    100% |           |
| django/otto/utils/auth.py                                              |       34 |        6 |     82% |     15-29 |
| django/otto/utils/cache.py                                             |       91 |       44 |     52% |25-30, 44, 55-60, 63-72, 75-80, 87-94, 99, 102, 105-107, 110-112 |
| django/otto/utils/common.py                                            |       19 |        3 |     84% | 22, 29-30 |
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
|                                                              **TOTAL** | **5434** | **2128** | **61%** |           |


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