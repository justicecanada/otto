# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                   |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django/case\_prep/forms.py                                             |        7 |        7 |      0% |      1-10 |
| django/case\_prep/models.py                                            |       34 |        2 |     94% |    23, 48 |
| django/case\_prep/views.py                                             |      194 |      118 |     39% |62, 86-101, 113, 135, 158-170, 174-207, 213-262, 266-278, 283-291, 295-306, 310-372, 377-402 |
| django/chat/forms.py                                                   |      125 |       23 |     82% |35, 43, 77, 97-109, 113-118, 127, 142, 146-151, 160, 324, 326-328 |
| django/chat/llm.py                                                     |       85 |       14 |     84% |43, 62-64, 70-72, 90-96, 216, 240 |
| django/chat/metrics/activity\_metrics.py                               |        4 |        0 |    100% |           |
| django/chat/metrics/feedback\_metrics.py                               |        3 |        0 |    100% |           |
| django/chat/models.py                                                  |      210 |       21 |     90% |34, 81, 84-85, 100-101, 240-243, 248-254, 298, 360-361, 365-367, 412, 422 |
| django/chat/prompts.py                                                 |       10 |        0 |    100% |           |
| django/chat/responses.py                                               |      247 |       77 |     69% |61, 65, 101, 181, 227-294, 319-320, 325-343, 346-359, 426, 472-506, 512-516, 556-557, 575, 579, 620-621 |
| django/chat/tasks.py                                                   |       71 |       54 |     24% |22-30, 35-106 |
| django/chat/utils.py                                                   |      225 |       43 |     81% |41, 126, 137-138, 186, 201, 203-204, 216-227, 254-267, 296-299, 314-316, 331, 365, 367, 419-426, 434, 456-466, 473 |
| django/chat/views.py                                                   |      333 |       50 |     85% |79-81, 121-123, 126-128, 137, 159, 179-186, 190-191, 229, 237, 267, 276-280, 375, 395-414, 445-450, 497, 502, 524, 550, 565-566, 622-630 |
| django/import\_timer.py                                                |        6 |        6 |      0% |       1-8 |
| django/laws/forms.py                                                   |       54 |        6 |     89% |24-29, 38, 52-57, 66 |
| django/laws/management/commands/load\_laws\_xml.py                     |      434 |      120 |     72% |26, 30-59, 74, 85-87, 103-104, 114-118, 146, 175, 236, 254, 256, 258, 277, 280, 282, 297-298, 300-301, 398-401, 411-429, 455-459, 471, 497, 549-550, 591-593, 704-708, 726-727, 729, 737, 777, 779, 797-799, 829-831, 834-836, 844-846, 848-850, 852-854, 856-858, 905-907, 923-925, 943-949, 997-1008, 1013, 1022-1023, 1046-1052 |
| django/laws/models.py                                                  |      104 |       22 |     79% |42-46, 90, 115-118, 152, 156-164, 168-169 |
| django/laws/prompts.py                                                 |        2 |        0 |    100% |           |
| django/laws/translation.py                                             |        5 |        0 |    100% |           |
| django/laws/utils.py                                                   |      103 |       87 |     16% |16-18, 26-36, 41-47, 51-66, 70-86, 93-106, 110-165, 173-174 |
| django/laws/views.py                                                   |      210 |       95 |     55% |60-90, 96-189, 198-209, 217, 239, 280, 282, 287-289, 301, 305, 331, 339, 347, 363-381, 423-431 |
| django/librarian/forms.py                                              |       85 |       25 |     71% |82, 105-112, 187-198, 204-213 |
| django/librarian/metrics/activity\_metrics.py                          |        9 |        9 |      0% |      1-50 |
| django/librarian/models.py                                             |      285 |       66 |     77% |45-47, 115, 117, 125, 127, 129, 135, 144-145, 152-153, 159-161, 179, 183, 223, 276-278, 281-282, 360-369, 379-384, 388, 392-395, 405-406, 415-427, 430-437, 440, 456 |
| django/librarian/tasks.py                                              |       92 |       39 |     58% |34-58, 65, 75, 84-94, 97, 117, 137-139, 150-153 |
| django/librarian/translation.py                                        |        8 |        0 |    100% |           |
| django/librarian/utils/markdown\_splitter.py                           |      170 |       10 |     94% |65, 68-70, 81, 116, 128, 242, 252, 259 |
| django/librarian/utils/process\_engine.py                              |      269 |       43 |     84% |39, 42-44, 49, 114-130, 135, 137, 139, 154, 170-179, 240, 264, 280-282, 331-335, 341-345, 393-394, 428 |
| django/librarian/views.py                                              |      286 |      143 |     50% |67-112, 119-141, 143-147, 154, 157-160, 173-191, 195-198, 217-233, 246-255, 287-296, 311, 318-320, 326, 332, 340, 347, 355, 361, 366, 374, 399-404, 410-415, 421-423, 431-435, 444-448, 457-461, 492, 503-510, 517-518 |
| django/otto/celery.py                                                  |       16 |        1 |     94% |        55 |
| django/otto/context\_processors.py                                     |        3 |        0 |    100% |           |
| django/otto/forms.py                                                   |       57 |        4 |     93% |72, 74, 158-159 |
| django/otto/management/commands/delete\_empty\_chats.py                |       19 |        1 |     95% |        29 |
| django/otto/management/commands/delete\_old\_chats.py                  |       20 |        2 |     90% |    31, 35 |
| django/otto/management/commands/reset\_app\_data.py                    |      124 |       20 |     84% |67-72, 90, 104-109, 129-134, 155-160, 174-175, 180-183, 198-203, 214 |
| django/otto/metrics/activity\_metrics.py                               |        2 |        0 |    100% |           |
| django/otto/metrics/feedback\_metrics.py                               |        3 |        0 |    100% |           |
| django/otto/models.py                                                  |      247 |       30 |     88% |26-28, 69-72, 101, 105-108, 143, 185, 201, 222, 229, 247, 308, 311, 347, 359, 365, 390, 394, 398, 402, 448-449, 463, 467, 471 |
| django/otto/rules.py                                                   |      121 |       18 |     85% |25, 42, 51, 89, 121, 149-153, 165, 167, 173, 178, 184, 188-189, 194 |
| django/otto/secure\_models.py                                          |      248 |       63 |     75% |21-22, 61, 86-100, 129-130, 135-136, 149-154, 183-224, 248, 268-269, 307, 337, 350, 359, 378, 393, 398, 403, 409-415, 418, 423, 437, 442, 447, 491-498, 517, 536-537, 549-552 |
| django/otto/settings.py                                                |      149 |       22 |     85% |37-39, 49-50, 205-214, 279-280, 361-367, 388, 415, 472-473 |
| django/otto/tasks.py                                                   |       21 |       21 |      0% |      1-35 |
| django/otto/templatetags/filters.py                                    |       10 |        0 |    100% |           |
| django/otto/templatetags/tags.py                                       |       10 |        1 |     90% |        18 |
| django/otto/translation.py                                             |       17 |        0 |    100% |           |
| django/otto/utils/auth.py                                              |       34 |        6 |     82% |     15-29 |
| django/otto/utils/cache.py                                             |       91 |       44 |     52% |25-30, 44, 55-60, 63-72, 75-80, 87-94, 99, 102, 105-107, 110-112 |
| django/otto/utils/common.py                                            |       19 |        1 |     95% |        22 |
| django/otto/utils/decorators.py                                        |       60 |        4 |     93% |24-25, 65, 87 |
| django/otto/utils/logging.py                                           |       15 |        0 |    100% |           |
| django/otto/views.py                                                   |      329 |       76 |     77% |41, 46-60, 101, 111-122, 169, 226, 278-281, 297-298, 312, 322-325, 354-364, 376-381, 384, 393, 395-398, 400-401, 403-406, 428, 436, 445, 512, 514, 516, 532-538, 548, 550, 552, 557-577, 616, 625-634 |
| django/template\_wizard/metrics/template\_wizard\_activity\_metrics.py |        2 |        0 |    100% |           |
| django/template\_wizard/models.py                                      |        9 |        0 |    100% |           |
| django/template\_wizard/translation.py                                 |        0 |        0 |    100% |           |
| django/template\_wizard/views.py                                       |       69 |       17 |     75% |63-70, 96, 146-153, 165-200 |
| django/template\_wizard/wizards/canlii\_wizard/utils.py                |      398 |      357 |     10% |81-143, 148-163, 172-180, 184-235, 239-251, 256-273, 278-294, 298-303, 307-394, 399-660, 665-974, 979-1200 |
| django/template\_wizard/wizards/canlii\_wizard/views.py                |      128 |      100 |     22% |50, 54-99, 112-117, 132-156, 161-213, 225-253, 258-291, 296-304 |
| django/text\_extractor/models.py                                       |       13 |        2 |     85% |    12, 23 |
| django/text\_extractor/utils.py                                        |      153 |       86 |     44% |49-72, 113-114, 132-287, 291-292 |
| django/text\_extractor/views.py                                        |      102 |       82 |     20% |31-34, 40-201, 205-218 |
|                                                              **TOTAL** | **6159** | **2038** | **67%** |           |


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