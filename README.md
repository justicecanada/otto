# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                   |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django/chat/forms.py                                                   |      145 |       23 |     84% |40, 48, 74, 94-106, 110-115, 124, 139, 143-148, 157, 328, 330-332 |
| django/chat/llm.py                                                     |       99 |       14 |     86% |71, 90-92, 98-100, 118-124, 244, 268 |
| django/chat/metrics/activity\_metrics.py                               |        4 |        0 |    100% |           |
| django/chat/metrics/feedback\_metrics.py                               |        3 |        0 |    100% |           |
| django/chat/models.py                                                  |      288 |       43 |     85% |36, 85, 88-90, 101-104, 236-239, 244-250, 323, 337-338, 343-344, 348-352, 359, 364, 370-371, 374, 402, 418, 480, 484-486, 509, 547, 557, 575-578 |
| django/chat/prompts.py                                                 |       10 |        0 |    100% |           |
| django/chat/responses.py                                               |      280 |       92 |     67% |64, 68, 106, 191, 239-312, 337-338, 343-359, 362-392, 432, 438-448, 485, 531-565, 571-575, 621, 648, 652, 693-694 |
| django/chat/tasks.py                                                   |       71 |       16 |     77% |22-30, 92-93, 96-101 |
| django/chat/templatetags/chat\_tags.py                                 |        5 |        0 |    100% |           |
| django/chat/utils.py                                                   |      265 |       52 |     80% |97, 109-110, 122-126, 170, 189, 191-192, 204-220, 227-228, 264-267, 273-280, 311-313, 328-330, 352, 424, 426, 443, 491-498, 506, 523-527, 533-543, 550 |
| django/chat/views.py                                                   |      436 |      102 |     77% |85-95, 101-108, 127, 152-154, 157-159, 183, 203-210, 214-215, 217, 257, 265, 296, 305-309, 395-446, 477-482, 534, 537, 591, 635, 659, 694-695, 748-756, 820-822, 828-842, 852-854, 863-866, 901-910, 914-916 |
| django/import\_timer.py                                                |        6 |        6 |      0% |       1-8 |
| django/laws/forms.py                                                   |       54 |        6 |     89% |24-29, 38, 52-57, 66 |
| django/laws/management/commands/load\_laws\_xml.py                     |      442 |      121 |     73% |29, 33-62, 77, 88-90, 106-109, 119-123, 151, 180, 241, 259, 261, 263, 282, 285, 287, 302-303, 305-306, 403-406, 416-434, 460-464, 476, 502, 554-555, 596-598, 714-720, 738-739, 741, 749, 789, 791, 809-811, 841-843, 846-848, 856-858, 860-862, 864-866, 868-870, 923-925, 941-943, 961-967, 1015-1026, 1031, 1040-1041, 1066-1072 |
| django/laws/models.py                                                  |      104 |       22 |     79% |42-46, 90, 115-118, 152, 156-164, 168-169 |
| django/laws/prompts.py                                                 |        2 |        0 |    100% |           |
| django/laws/translation.py                                             |        5 |        0 |    100% |           |
| django/laws/utils.py                                                   |       70 |       24 |     66% |16-18, 26-36, 41-47, 59-66, 77, 93-95 |
| django/laws/views.py                                                   |      210 |       92 |     56% |64-94, 100-193, 202-213, 221, 243, 284, 286, 291-293, 305, 309, 335, 343, 351, 361, 368, 427-435 |
| django/librarian/forms.py                                              |       85 |       30 |     65% |78-83, 105-112, 187-198, 204-213 |
| django/librarian/metrics/activity\_metrics.py                          |        9 |        9 |      0% |      1-50 |
| django/librarian/models.py                                             |      288 |       81 |     72% |53-55, 123, 125, 133, 135, 137, 143, 152-153, 160-161, 164-166, 184, 188, 230, 283-289, 292-293, 378-379, 383-392, 402-407, 411, 421-426, 429-441, 444-451, 454, 470, 473-478, 481-486, 491-494 |
| django/librarian/tasks.py                                              |      113 |       46 |     59% |42-75, 82, 92, 105, 115, 135, 145, 157-159, 170-173, 186-193 |
| django/librarian/translation.py                                        |        8 |        0 |    100% |           |
| django/librarian/utils/markdown\_splitter.py                           |      183 |       10 |     95% |72, 75-77, 88, 123, 137, 260, 270, 277 |
| django/librarian/utils/process\_engine.py                              |      422 |       57 |     86% |44-46, 50-55, 145, 150, 160-161, 165, 171, 174, 181, 183, 185, 187, 193, 195, 197, 221, 234, 246-247, 260-269, 315-321, 358, 382, 398-400, 449-453, 459-463, 467, 515-516, 550, 637, 659 |
| django/librarian/views.py                                              |      287 |      173 |     40% |68-115, 121-169, 180-200, 204-207, 228-246, 258-267, 299-308, 323, 330-332, 338, 344, 352, 359, 367, 373, 378, 386, 411-416, 422-424, 432-436, 445-460, 472-509, 517-524, 531-532 |
| django/otto/celery.py                                                  |       16 |        1 |     94% |        69 |
| django/otto/context\_processors.py                                     |       10 |        4 |     60% |      9-13 |
| django/otto/forms.py                                                   |       68 |        4 |     94% |72, 74, 202-203 |
| django/otto/management/commands/delete\_empty\_chats.py                |       19 |        1 |     95% |        29 |
| django/otto/management/commands/delete\_old\_chats.py                  |       20 |        2 |     90% |    31, 35 |
| django/otto/management/commands/delete\_text\_extractor\_files.py      |       18 |        0 |    100% |           |
| django/otto/management/commands/reset\_app\_data.py                    |      124 |       20 |     84% |67-72, 90, 104-109, 129-134, 155-160, 174-175, 180-183, 198-203, 214 |
| django/otto/management/commands/update\_exchange\_rate.py              |       22 |        2 |     91% |     35-37 |
| django/otto/metrics/activity\_metrics.py                               |        2 |        0 |    100% |           |
| django/otto/metrics/feedback\_metrics.py                               |        3 |        3 |      0% |       1-8 |
| django/otto/models.py                                                  |      279 |       30 |     89% |26-28, 76-79, 112, 116-119, 154, 193, 196, 212, 233, 240, 258, 381, 384, 436, 442, 466, 470, 474, 478, 524-525, 539, 543, 547 |
| django/otto/rules.py                                                   |      138 |       23 |     83% |26, 42, 49, 51, 120, 157, 185-189, 195, 200-204, 209, 214, 220, 224-225, 230 |
| django/otto/secure\_models.py                                          |      248 |       91 |     63% |21-22, 61, 86-100, 129-130, 135-136, 149-154, 183-224, 248, 268-269, 307, 337, 350, 359, 378, 393, 398, 403, 409-415, 418, 423, 437, 442, 447, 454-482, 485-486, 491-498, 501-502, 508-522, 536-537, 542-552, 557-558, 561-562 |
| django/otto/settings.py                                                |      160 |       24 |     85% |38-41, 51-52, 218-227, 301, 314, 371-378, 407, 447, 504-505 |
| django/otto/tasks.py                                                   |       37 |       12 |     68% |11, 16, 21-23, 28, 33, 38, 43, 48, 61-63 |
| django/otto/templatetags/filters.py                                    |       10 |        1 |     90% |         8 |
| django/otto/templatetags/tags.py                                       |       10 |        1 |     90% |        18 |
| django/otto/translation.py                                             |       17 |        0 |    100% |           |
| django/otto/utils/auth.py                                              |       36 |        6 |     83% |     18-32 |
| django/otto/utils/cache.py                                             |       91 |       44 |     52% |25-30, 44, 55-60, 63-72, 75-80, 87-94, 99, 102, 105-107, 110-112 |
| django/otto/utils/common.py                                            |       30 |        0 |    100% |           |
| django/otto/utils/decorators.py                                        |       60 |        4 |     93% |24-25, 65, 87 |
| django/otto/utils/logging.py                                           |       15 |        0 |    100% |           |
| django/otto/utils/middleware.py                                        |       17 |        1 |     94% |        23 |
| django/otto/views.py                                                   |      536 |      114 |     79% |58, 63, 68-82, 123, 133-144, 159, 280, 381, 433-436, 452-453, 477, 487-490, 519-529, 541-546, 549, 558, 560-563, 565-566, 568-571, 580, 594, 602, 611, 760, 762, 764, 778, 780, 791-794, 804-810, 820, 822, 824, 829-849, 869-877, 886-906, 994, 1015-1018, 1025, 1058-1081 |
| django/postgres\_wrapper/base.py                                       |        6 |        0 |    100% |           |
| django/template\_wizard/metrics/template\_wizard\_activity\_metrics.py |        2 |        0 |    100% |           |
| django/template\_wizard/models.py                                      |        9 |        0 |    100% |           |
| django/template\_wizard/translation.py                                 |        0 |        0 |    100% |           |
| django/template\_wizard/views.py                                       |       69 |       17 |     75% |63-70, 96, 146-153, 165-200 |
| django/template\_wizard/wizards/canlii\_wizard/utils.py                |      401 |      360 |     10% |81-143, 148-163, 167-177, 181-232, 236-248, 253-270, 275-291, 295-300, 304-391, 396-657, 662-971, 976-1197 |
| django/template\_wizard/wizards/canlii\_wizard/views.py                |      128 |      100 |     22% |50, 54-99, 112-117, 132-156, 161-213, 225-253, 258-291, 296-304 |
| django/text\_extractor/models.py                                       |       17 |        1 |     94% |        28 |
| django/text\_extractor/tasks.py                                        |       18 |        2 |     89% |     32-33 |
| django/text\_extractor/utils.py                                        |      208 |       40 |     81% |57-80, 121-122, 174, 211, 285-287, 341-345, 352-353, 359, 365-369 |
| django/text\_extractor/views.py                                        |      108 |       21 |     81% |41, 59-74, 84, 98-106, 119-125, 142, 146, 163, 173, 193-194 |
|                                                              **TOTAL** | **6846** | **1948** | **72%** |           |


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