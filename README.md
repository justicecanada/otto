# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                   |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django/chat/forms.py                                                   |      142 |       23 |     84% |40, 48, 82, 102-114, 118-123, 132, 147, 151-156, 165, 329, 331-333 |
| django/chat/llm.py                                                     |       85 |       14 |     84% |43, 62-64, 70-72, 90-96, 216, 240 |
| django/chat/metrics/activity\_metrics.py                               |        4 |        0 |    100% |           |
| django/chat/metrics/feedback\_metrics.py                               |        3 |        0 |    100% |           |
| django/chat/models.py                                                  |      275 |       69 |     75% |36, 82, 85-86, 89-91, 102-104, 230-233, 238-244, 249-263, 322-326, 334-344, 348-352, 355-359, 362-371, 415, 477-478, 482-484, 529, 539, 552-555 |
| django/chat/prompts.py                                                 |       10 |        0 |    100% |           |
| django/chat/responses.py                                               |      247 |       77 |     69% |61, 65, 101, 181, 227-294, 319-320, 325-343, 346-359, 426, 472-506, 512-516, 556-557, 575, 579, 620-621 |
| django/chat/tasks.py                                                   |       71 |       54 |     24% |22-30, 35-106 |
| django/chat/templatetags/chat\_tags.py                                 |        5 |        1 |     80% |         8 |
| django/chat/utils.py                                                   |      227 |       43 |     81% |43, 128, 139-140, 188, 203, 205-206, 218-229, 256-269, 298-301, 316-318, 333, 367, 369, 421-428, 436, 458-468, 475 |
| django/chat/views.py                                                   |      409 |      105 |     74% |84-94, 133-135, 138-140, 149, 171, 191-198, 202-203, 243, 251, 281, 290-294, 378-431, 462-467, 528, 531, 575, 619, 625, 641, 648-649, 702-710, 750, 765-775, 780-782, 790-793, 806-843 |
| django/import\_timer.py                                                |        6 |        6 |      0% |       1-8 |
| django/laws/forms.py                                                   |       54 |        6 |     89% |24-29, 38, 52-57, 66 |
| django/laws/management/commands/load\_laws\_xml.py                     |      436 |      120 |     72% |29, 33-62, 77, 88-90, 106-109, 119-123, 151, 180, 241, 259, 261, 263, 282, 285, 287, 302-303, 305-306, 403-406, 416-434, 460-464, 476, 502, 554-555, 596-598, 709-713, 731-732, 734, 742, 782, 784, 802-804, 834-836, 839-841, 849-851, 853-855, 857-859, 861-863, 912-914, 930-932, 950-956, 1004-1015, 1020, 1029-1030, 1055-1061 |
| django/laws/models.py                                                  |      104 |       22 |     79% |42-46, 90, 115-118, 152, 156-164, 168-169 |
| django/laws/prompts.py                                                 |        2 |        0 |    100% |           |
| django/laws/translation.py                                             |        5 |        0 |    100% |           |
| django/laws/utils.py                                                   |      103 |       87 |     16% |16-18, 26-36, 41-47, 51-66, 70-86, 93-106, 110-165, 173-174 |
| django/laws/views.py                                                   |      210 |       95 |     55% |60-90, 96-189, 198-209, 217, 239, 280, 282, 287-289, 301, 305, 331, 339, 347, 363-381, 423-431 |
| django/librarian/forms.py                                              |       85 |       30 |     65% |78-83, 105-112, 187-198, 204-213 |
| django/librarian/metrics/activity\_metrics.py                          |        9 |        9 |      0% |      1-50 |
| django/librarian/models.py                                             |      288 |       82 |     72% |53-55, 123, 125, 133, 135, 137, 143, 152-153, 160-161, 164-166, 184, 188, 228, 281-287, 290-291, 376-377, 381-390, 400-405, 409, 414, 419-424, 427-439, 442-449, 452, 468, 471-476, 479-484, 489-492 |
| django/librarian/tasks.py                                              |      109 |       50 |     54% |36-59, 66, 76, 84, 87-101, 104, 124, 134, 146-148, 159-162, 175-182 |
| django/librarian/translation.py                                        |        8 |        0 |    100% |           |
| django/librarian/utils/markdown\_splitter.py                           |      183 |       10 |     95% |72, 75-77, 88, 123, 137, 260, 270, 277 |
| django/librarian/utils/process\_engine.py                              |      314 |       49 |     84% |39, 42-44, 48-53, 114-130, 135, 137, 139, 143, 163, 176, 190-199, 263, 287, 303-305, 354-358, 364-368, 372, 420-421, 455 |
| django/librarian/views.py                                              |      281 |      168 |     40% |67-112, 118-162, 173-191, 195-198, 217-233, 246-255, 287-296, 311, 318-320, 326, 332, 340, 347, 355, 361, 366, 374, 399-404, 410-412, 420-424, 433-448, 460-482, 490-497, 504-505 |
| django/otto/celery.py                                                  |       16 |        1 |     94% |        64 |
| django/otto/context\_processors.py                                     |        9 |        5 |     44% |      7-18 |
| django/otto/forms.py                                                   |       57 |        4 |     93% |72, 74, 158-159 |
| django/otto/management/commands/delete\_empty\_chats.py                |       19 |        1 |     95% |        29 |
| django/otto/management/commands/delete\_old\_chats.py                  |       20 |        2 |     90% |    31, 35 |
| django/otto/management/commands/delete\_text\_extractor\_files.py      |       18 |        0 |    100% |           |
| django/otto/management/commands/reset\_app\_data.py                    |      124 |       20 |     84% |67-72, 90, 104-109, 129-134, 155-160, 174-175, 180-183, 198-203, 214 |
| django/otto/metrics/activity\_metrics.py                               |        2 |        0 |    100% |           |
| django/otto/metrics/feedback\_metrics.py                               |        3 |        0 |    100% |           |
| django/otto/models.py                                                  |      248 |       30 |     88% |26-28, 76-79, 108, 112-115, 150, 192, 208, 229, 236, 254, 315, 318, 354, 366, 372, 397, 401, 405, 409, 454-455, 469, 473, 477 |
| django/otto/rules.py                                                   |      122 |       22 |     82% |25, 43, 52, 90, 122, 150-154, 160, 165-169, 174, 179, 185, 189-190, 195 |
| django/otto/secure\_models.py                                          |      248 |       91 |     63% |21-22, 61, 86-100, 129-130, 135-136, 149-154, 183-224, 248, 268-269, 307, 337, 350, 359, 378, 393, 398, 403, 409-415, 418, 423, 437, 442, 447, 454-482, 485-486, 491-498, 501-502, 508-522, 536-537, 542-552, 557-558, 561-562 |
| django/otto/settings.py                                                |      152 |       23 |     85% |38-41, 51-52, 207-216, 280-281, 347-353, 382, 422, 479-480 |
| django/otto/tasks.py                                                   |       27 |       27 |      0% |      1-45 |
| django/otto/templatetags/filters.py                                    |       10 |        0 |    100% |           |
| django/otto/templatetags/tags.py                                       |       10 |        1 |     90% |        18 |
| django/otto/translation.py                                             |       17 |        0 |    100% |           |
| django/otto/utils/auth.py                                              |       34 |        6 |     82% |     15-29 |
| django/otto/utils/cache.py                                             |       91 |       44 |     52% |25-30, 44, 55-60, 63-72, 75-80, 87-94, 99, 102, 105-107, 110-112 |
| django/otto/utils/common.py                                            |       19 |        1 |     95% |        22 |
| django/otto/utils/decorators.py                                        |       60 |        4 |     93% |24-25, 65, 87 |
| django/otto/utils/logging.py                                           |       15 |        0 |    100% |           |
| django/otto/views.py                                                   |      331 |       77 |     77% |40, 45, 50-64, 105, 115-126, 173, 230, 282-285, 301-302, 316, 326-329, 358-368, 380-385, 388, 397, 399-402, 404-405, 407-410, 432, 440, 449, 516, 518, 520, 536-542, 552, 554, 556, 561-581, 620, 629-638 |
| django/template\_wizard/metrics/template\_wizard\_activity\_metrics.py |        2 |        0 |    100% |           |
| django/template\_wizard/models.py                                      |        9 |        0 |    100% |           |
| django/template\_wizard/translation.py                                 |        0 |        0 |    100% |           |
| django/template\_wizard/views.py                                       |       69 |       17 |     75% |63-70, 96, 146-153, 165-200 |
| django/template\_wizard/wizards/canlii\_wizard/utils.py                |      401 |      360 |     10% |81-143, 148-163, 167-177, 181-232, 236-248, 253-270, 275-291, 295-300, 304-391, 396-657, 662-971, 976-1197 |
| django/template\_wizard/wizards/canlii\_wizard/views.py                |      128 |      100 |     22% |50, 54-99, 112-117, 132-156, 161-213, 225-253, 258-291, 296-304 |
| django/text\_extractor/models.py                                       |       13 |        1 |     92% |        23 |
| django/text\_extractor/utils.py                                        |      158 |       88 |     44% |54-77, 118-119, 137-294, 298-299 |
| django/text\_extractor/views.py                                        |      106 |       84 |     21% |33-36, 41-208, 212-225 |
|                                                              **TOTAL** | **6213** | **2129** | **66%** |           |


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