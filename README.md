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
| django/chat/models.py                                                  |      185 |       22 |     88% |30, 77, 80-81, 96-97, 236-239, 244-250, 294, 335-336, 340-342, 346, 384, 394 |
| django/chat/prompts.py                                                 |       10 |        0 |    100% |           |
| django/chat/responses.py                                               |      247 |       77 |     69% |61, 65, 101, 181, 227-294, 319-320, 325-343, 346-359, 426, 466-500, 506-510, 550-551, 569, 573, 614-615 |
| django/chat/tasks.py                                                   |       74 |       57 |     23% |22-30, 35-109 |
| django/chat/utils.py                                                   |      226 |       57 |     75% |41, 119-148, 189, 204, 206-207, 219-230, 257-270, 299-302, 317-319, 334, 368, 370, 422-429, 437, 459-469, 476 |
| django/chat/views.py                                                   |      315 |       65 |     79% |79-81, 118-120, 123-125, 134, 156, 176-183, 187-188, 225, 233, 263, 272-276, 359-403, 434-439, 486, 491, 513, 539, 554-555, 611-619, 628-629 |
| django/import\_timer.py                                                |        6 |        6 |      0% |       1-8 |
| django/laws/forms.py                                                   |       54 |        6 |     89% |24-29, 38, 52-57, 66 |
| django/laws/management/commands/load\_laws\_xml.py                     |      434 |      120 |     72% |26, 30-59, 74, 85-87, 103-104, 114-118, 146, 175, 236, 254, 256, 258, 277, 280, 282, 297-298, 300-301, 398-401, 411-429, 455-459, 471, 497, 549-550, 591-593, 704-708, 726-727, 729, 737, 777, 779, 797-799, 829-831, 834-836, 844-846, 848-850, 852-854, 856-858, 905-907, 923-925, 943-949, 997-1008, 1013, 1022-1023, 1046-1052 |
| django/laws/models.py                                                  |      104 |       22 |     79% |42-46, 90, 115-118, 152, 156-164, 168-169 |
| django/laws/prompts.py                                                 |        2 |        0 |    100% |           |
| django/laws/translation.py                                             |        5 |        0 |    100% |           |
| django/laws/utils.py                                                   |      103 |       87 |     16% |16-18, 26-36, 41-47, 51-66, 70-86, 93-106, 110-165, 173-174 |
| django/laws/views.py                                                   |      208 |       96 |     54% |60-90, 95-188, 197-208, 215, 237, 278, 280, 285-287, 296, 299, 303, 329, 337, 345, 361-379, 421-429 |
| django/librarian/forms.py                                              |       85 |       30 |     65% |78-83, 105-112, 187-198, 204-213 |
| django/librarian/metrics/activity\_metrics.py                          |        9 |        9 |      0% |      1-50 |
| django/librarian/models.py                                             |      277 |       80 |     71% |46-48, 116, 118, 126, 128, 130, 136, 145-146, 153-154, 160-162, 180, 184, 224, 277-279, 282-283, 349, 361-370, 374, 380, 386-391, 395, 399-402, 405-406, 409-414, 417-429, 432-439, 442, 458, 461-465 |
| django/librarian/tasks.py                                              |       89 |       34 |     62% |34-55, 62, 72, 81-91, 94, 114, 135, 146-149 |
| django/librarian/translation.py                                        |        8 |        0 |    100% |           |
| django/librarian/utils/process\_engine.py                              |      326 |      135 |     59% |25, 28-30, 34-39, 97-113, 118, 120, 122, 133-137, 139, 141, 143, 153, 159-161, 167-174, 188-194, 198-217, 221-237, 241-259, 304, 352-356, 360-366, 424-425, 492-590 |
| django/librarian/views.py                                              |      271 |      159 |     41% |65-110, 116-160, 171-189, 193-196, 215-231, 244-253, 285-294, 309, 316-318, 324, 330, 338, 345, 353, 359, 364, 372, 396-401, 406-411, 417-419, 427-430, 438-442, 450-454, 465-476, 484-491, 498-499 |
| django/otto/celery.py                                                  |       16 |        1 |     94% |        35 |
| django/otto/context\_processors.py                                     |        3 |        0 |    100% |           |
| django/otto/forms.py                                                   |       55 |        8 |     85% |72, 74, 140-149 |
| django/otto/management/commands/reset\_app\_data.py                    |      124 |       20 |     84% |67-72, 90, 104-109, 129-134, 155-160, 174-175, 180-183, 198-203, 214 |
| django/otto/metrics/activity\_metrics.py                               |        2 |        0 |    100% |           |
| django/otto/metrics/feedback\_metrics.py                               |        3 |        0 |    100% |           |
| django/otto/models.py                                                  |      236 |       31 |     87% |26-28, 67-70, 89, 93-96, 131, 173, 189, 210, 217, 235, 296, 299, 335, 347, 353, 357, 361, 365, 369, 378, 424-425, 439, 443, 447 |
| django/otto/rules.py                                                   |      121 |       22 |     82% |25, 42, 51, 89, 121, 149-153, 159, 164-168, 173, 178, 184, 188-189, 194 |
| django/otto/secure\_models.py                                          |      248 |       63 |     75% |21-22, 61, 86-100, 129-130, 135-136, 149-154, 183-224, 248, 268-269, 307, 337, 350, 359, 378, 393, 398, 403, 409-415, 418, 423, 437, 442, 447, 491-498, 517, 536-537, 549-552 |
| django/otto/settings.py                                                |      148 |       22 |     85% |37-39, 49-50, 204-213, 278-279, 360-366, 387, 414, 471-472 |
| django/otto/tasks.py                                                   |        8 |        8 |      0% |      1-13 |
| django/otto/templatetags/filters.py                                    |       10 |        0 |    100% |           |
| django/otto/templatetags/tags.py                                       |       10 |        1 |     90% |        18 |
| django/otto/translation.py                                             |       17 |        0 |    100% |           |
| django/otto/utils/auth.py                                              |       34 |        6 |     82% |     15-29 |
| django/otto/utils/cache.py                                             |       91 |       44 |     52% |25-30, 44, 55-60, 63-72, 75-80, 87-94, 99, 102, 105-107, 110-112 |
| django/otto/utils/common.py                                            |       19 |        3 |     84% | 22, 29-30 |
| django/otto/utils/decorators.py                                        |       46 |        4 |     91% |23-24, 64, 86 |
| django/otto/utils/logging.py                                           |       15 |        0 |    100% |           |
| django/otto/views.py                                                   |      310 |      154 |     50% |41, 46-60, 101, 111-122, 169, 224, 270-273, 277-281, 291, 294-297, 303-304, 334-351, 356-368, 373-437, 452-656 |
| django/template\_wizard/metrics/template\_wizard\_activity\_metrics.py |        2 |        0 |    100% |           |
| django/template\_wizard/models.py                                      |        9 |        0 |    100% |           |
| django/template\_wizard/translation.py                                 |        0 |        0 |    100% |           |
| django/template\_wizard/views.py                                       |       69 |       17 |     75% |63-70, 96, 146-153, 165-200 |
| django/template\_wizard/wizards/canlii\_wizard/utils.py                |      398 |      357 |     10% |81-143, 148-163, 172-180, 184-235, 239-251, 256-273, 278-294, 298-303, 307-394, 399-660, 665-974, 979-1200 |
| django/template\_wizard/wizards/canlii\_wizard/views.py                |      126 |      100 |     21% |49, 53-98, 111-116, 130-154, 159-211, 223-251, 256-289, 294-302 |
| django/text\_extractor/models.py                                       |       14 |        2 |     86% |    12, 24 |
| django/text\_extractor/utils.py                                        |      149 |       84 |     44% |48-71, 112-113, 131-287 |
| django/text\_extractor/views.py                                        |      104 |       86 |     17% |29-32, 37-207, 211-226 |
|                                                              **TOTAL** | **5877** | **2259** | **62%** |           |


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