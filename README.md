# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                   |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django/chat/forms.py                                                   |      144 |       23 |     84% |40, 48, 82, 102-114, 118-123, 132, 147, 151-156, 165, 336, 338-340 |
| django/chat/llm.py                                                     |       85 |       14 |     84% |43, 62-64, 70-72, 90-96, 216, 240 |
| django/chat/metrics/activity\_metrics.py                               |        4 |        0 |    100% |           |
| django/chat/metrics/feedback\_metrics.py                               |        3 |        0 |    100% |           |
| django/chat/models.py                                                  |      287 |       43 |     85% |37, 83, 86-88, 99-101, 233-236, 241-247, 320, 334-335, 340-341, 345-349, 356, 361, 367-368, 371, 399, 415, 477, 481-483, 506, 544, 554, 572-575 |
| django/chat/prompts.py                                                 |       10 |        0 |    100% |           |
| django/chat/responses.py                                               |      277 |       89 |     68% |64, 68, 106, 191, 239-310, 335-336, 341-357, 360-390, 430, 436-446, 483, 529-563, 569-573, 619, 646, 650, 691-692 |
| django/chat/tasks.py                                                   |       71 |       16 |     77% |22-30, 92-93, 96-101 |
| django/chat/templatetags/chat\_tags.py                                 |        5 |        0 |    100% |           |
| django/chat/utils.py                                                   |      243 |       46 |     81% |76, 88-89, 140, 159, 161-162, 174-190, 197, 233-236, 242-249, 280-282, 297-299, 321, 393, 395, 412, 460-467, 475, 492-496, 502-512, 519 |
| django/chat/views.py                                                   |      418 |       95 |     77% |85-95, 101-108, 127, 152-154, 157-159, 182, 202-209, 213-214, 252, 260, 291, 300-304, 390-441, 472-477, 544, 547, 593, 637, 643, 658-659, 712-720, 784-786, 792-794, 803-806, 842-851, 855-857 |
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
| django/librarian/tasks.py                                              |      113 |       46 |     59% |40-73, 80, 90, 103, 113, 133, 143, 155-157, 168-171, 184-191 |
| django/librarian/translation.py                                        |        8 |        0 |    100% |           |
| django/librarian/utils/markdown\_splitter.py                           |      183 |       10 |     95% |72, 75-77, 88, 123, 137, 260, 270, 277 |
| django/librarian/utils/process\_engine.py                              |      422 |       57 |     86% |44-46, 50-55, 145, 150, 160-161, 165, 171, 174, 181, 183, 185, 187, 193, 195, 197, 221, 234, 246-247, 260-269, 315-321, 358, 382, 398-400, 449-453, 459-463, 467, 515-516, 550, 637, 659 |
| django/librarian/views.py                                              |      287 |      173 |     40% |68-113, 119-163, 174-192, 196-199, 218-234, 246-255, 287-296, 311, 318-320, 326, 332, 340, 347, 355, 361, 366, 374, 399-404, 410-412, 420-424, 433-448, 460-497, 505-512, 519-520 |
| django/otto/celery.py                                                  |       16 |        1 |     94% |        69 |
| django/otto/context\_processors.py                                     |       10 |        4 |     60% |      9-13 |
| django/otto/forms.py                                                   |       68 |        4 |     94% |72, 74, 202-203 |
| django/otto/management/commands/delete\_empty\_chats.py                |       19 |        1 |     95% |        29 |
| django/otto/management/commands/delete\_old\_chats.py                  |       20 |        2 |     90% |    31, 35 |
| django/otto/management/commands/delete\_text\_extractor\_files.py      |       18 |        0 |    100% |           |
| django/otto/management/commands/reset\_app\_data.py                    |      124 |       20 |     84% |67-72, 90, 104-109, 129-134, 155-160, 174-175, 180-183, 198-203, 214 |
| django/otto/management/commands/update\_exchange\_rate.py              |       22 |        2 |     91% |     35-37 |
| django/otto/metrics/activity\_metrics.py                               |        2 |        0 |    100% |           |
| django/otto/metrics/feedback\_metrics.py                               |        3 |        0 |    100% |           |
| django/otto/models.py                                                  |      279 |       30 |     89% |26-28, 76-79, 112, 116-119, 154, 193, 196, 212, 233, 240, 258, 381, 384, 436, 442, 467, 471, 475, 479, 525-526, 540, 544, 548 |
| django/otto/rules.py                                                   |      134 |       23 |     83% |25, 41, 48, 50, 112, 144, 172-176, 182, 187-191, 196, 201, 207, 211-212, 217 |
| django/otto/secure\_models.py                                          |      248 |       91 |     63% |21-22, 61, 86-100, 129-130, 135-136, 149-154, 183-224, 248, 268-269, 307, 337, 350, 359, 378, 393, 398, 403, 409-415, 418, 423, 437, 442, 447, 454-482, 485-486, 491-498, 501-502, 508-522, 536-537, 542-552, 557-558, 561-562 |
| django/otto/settings.py                                                |      153 |       22 |     86% |38-41, 51-52, 218-227, 353-360, 389, 429, 486-487 |
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
| django/otto/views.py                                                   |      472 |       94 |     80% |59, 64, 69-83, 124, 134-145, 160, 281, 382, 434-437, 453-454, 478, 488-491, 520-530, 542-547, 550, 559, 561-564, 566-567, 569-572, 594, 602, 611, 678, 680, 682, 698-704, 714, 716, 718, 723-743, 782, 791-800, 885, 906-929, 936 |
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
|                                                              **TOTAL** | **6706** | **1907** | **72%** |           |


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