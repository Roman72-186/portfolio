// js/content/cardsRegistry.js

import { BLOCKS, SUBBLOCKS, CONTENT_TYPES } from "./contentTypes.js";

export const CARDS = {
  test_universal_card: {
    id: "test_universal_card",
    title: "Тест universal viewer",
    desc: "Тестовая карточка для проверки ARCH",
    preview: "",
    viewerProfile: "arch",

    blocks: {
      [BLOCKS.MODEL_3D]: {
        subblocks: {
          [SUBBLOCKS.MODEL_3D]: {
            type: CONTENT_TYPES.MODEL,
            items: [
              {
                id: "kapitel2",
                sourcePath: "models/kapitel2.gltf",
                textures: {
                  base: "textures/kapitel2/BaseColor.jpg",
                  normal: "textures/kapitel2/Normal.jpg",
                  rough: "textures/kapitel2/Roughness.jpg",
                  metalness: 0,
                  roughness: 1,
                  envIntensity: 0.75
                }
              }
            ]
          },

          [SUBBLOCKS.VIDEOS]: {
            type: CONTENT_TYPES.VIDEOS,
            items: [
              "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/textures/kapitel2/v1.mp4",
              "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/textures/kapitel2/v2.mp4"
            ]
          }
        }
      },

      [BLOCKS.SCHEMES]: {
        subblocks: {
          [SUBBLOCKS.SCHEMES]: {
            type: CONTENT_TYPES.IMAGES,
            items: [
              "textures/kapitel2/s1.jpg",
              "textures/kapitel2/s2.jpg"
            ]
          },

          [SUBBLOCKS.VIDEOS]: {
            type: CONTENT_TYPES.VIDEOS,
            items: [
              "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/textures/kapitel2/v1.mp4"
            ]
          }
        }
      }
    }
  },

  test_universal_inset_card: {
    id: "test_universal_inset_card",
    title: "Тест universal inset",
    desc: "Тестовая universal-врезка",
    preview: "",
    viewerProfile: "inset",

    blocks: {
      [BLOCKS.MODEL_3D]: {
        subblocks: {
          [SUBBLOCKS.MODEL_3D]: {
            type: CONTENT_TYPES.MODEL,
            items: [
              {
                id: "inset_3",
                sourcePath: "models/3.gltf",
                rendererSettings: {
                  opacityMaterialName: "1",
                  primarySectionMaterialNames: ["2", "3"],
                  auxSectionMaterialNames: ["4"],
                  materialColors: {
                    "2": "#000000",
                    "3": "#000000",
                    "4": "#000000"
                  },
                  cad: {
                    fromNodes: true,
                    lines: [
                      ["a", "b"]
                    ]
                  }
                }
              }
            ]
          },

          [SUBBLOCKS.VIDEOS]: {
            type: CONTENT_TYPES.VIDEOS,
            items: [
              "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/textures/doric/v1.mp4"
            ]
          }
        }
      },

      [BLOCKS.SCHEMES]: {
        subblocks: {
          [SUBBLOCKS.SCHEMES]: {
            type: CONTENT_TYPES.IMAGES,
            items: [
              "textures/3/SS1.jpg",
              "textures/3/SS2.jpg",
              "textures/3/SS3.jpg"
            ]
          }
        }
      }
    }
  },

  test_universal_room_card: {
    id: "test_universal_room_card",
    title: "Тест universal rooms",
    desc: "Тестовая universal-карточка интерьера",
    preview: "",
    viewerProfile: "rooms",

    blocks: {
      [BLOCKS.MODEL_3D]: {
        subblocks: {
          [SUBBLOCKS.MODEL_3D]: {
            type: CONTENT_TYPES.MODEL,
            items: [
              {
                id: "room_1",
                sourcePath: "models/rooms/1/1.gltf",
                textures: {
                  base: "models/rooms/1/1.jpg",
                  roughness: 1
                }
              }
            ]
          },

          [SUBBLOCKS.SCHEMES]: {
            type: CONTENT_TYPES.IMAGES,
            items: [
              "textures/kapitel2/s1.jpg",
              "textures/kapitel2/s2.jpg"
            ]
          },

          [SUBBLOCKS.VIDEOS]: {
            type: CONTENT_TYPES.VIDEOS,
            items: [
              "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/textures/doric/v1.mp4"
            ]
          }
        }
      }
    }
  },

kapitel2_universal: {
  id: "kapitel2_universal",
  title: "Малая капитель",
  desc: "Теория по построению малой капители",
  preview:
    "content/arch/kapitel2/preview/preview.png",

  viewerProfile: "arch",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,

          items: [
            {
              id: "kapitel2",

              sourcePath:
                "content/arch/kapitel2/model/kapitel2.gltf",

              textures: {
                base:
                  "content/arch/kapitel2/textures/BaseColor.jpg",

                normal:
                  "content/arch/kapitel2/textures/Normal.jpg",

                rough:
                  "content/arch/kapitel2/textures/Roughness.jpg",

                metalness: 0,
                roughness: 1,
                envIntensity: 0.75
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,

          items: [
            "content/arch/kapitel2/schemes/s1.jpg",
            "content/arch/kapitel2/schemes/s2.jpg"
          ]
        },

        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,

          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/kapitel2/videos/scheme/v1.mp4"
          ]
        }
      }
    },

    [BLOCKS.DRAWING]: {
      subblocks: {
        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,

          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/kapitel2/videos/drawing/v2.mp4"
          ]
        }
      }
    }
  }
},
kapitel1_universal: {
  id: "kapitel1_universal",
  title: "Большая капитель",
  preview:
    "content/arch/kapitel1/preview/preview.png",

  viewerProfile: "arch",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,

          items: [
            {
              id: "kapitel1",

              sourcePath:
                "content/arch/kapitel1/model/kapitel1.gltf",

              textures: {
                base:
                  "content/arch/kapitel1/textures/BaseColor.jpg",

                normal:
                  "content/arch/kapitel1/textures/Normal.jpg",

                rough:
                  "content/arch/kapitel1/textures/Roughness.jpg",

                metalness: 0,
                roughness: 1,
                envIntensity: 0.75
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,

          items: [
            "content/arch/kapitel1/schemes/s1.jpg",
            "content/arch/kapitel1/schemes/s2.jpg"
          ]
        },

        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,

          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/kapitel1/videos/scheme/v1.mp4"
          ]
        }
      }
    },

    [BLOCKS.DRAWING]: {
      subblocks: {
        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,

          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/kapitel1/videos/drawing/v2.mp4"
          ]
        }
      }
    }
  }
},

vase1_universal: {
  id: "vase1_universal",
  title: "Малая ваза",
  desc: "СПбГАСУ",
  preview: "content/arch/vase1/preview/preview.png",

  viewerProfile: "arch",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "vase1",
              sourcePath: "content/arch/vase1/model/vase1.gltf",
              textures: {
                base: "content/arch/vase1/textures/BaseColor.jpg",
                normal: "content/arch/vase1/textures/Normal.jpg",
                rough: "content/arch/vase1/textures/Roughness.jpg",
                metalness: 0,
                roughness: 1,
                envIntensity: 0.75
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,
          items: [
            "content/arch/vase1/schemes/s1.jpg",
            "content/arch/vase1/schemes/s2.jpg"
          ]
        },

        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,
          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/vase1/videos/scheme/v1.mp4"
          ]
        }
      }
    },

    [BLOCKS.DRAWING]: {
      subblocks: {
        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,
          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/vase1/videos/drawing/v2.mp4"
          ]
        }
      }
    }
  }
},

vase2_universal: {
  id: "vase2_universal",
  title: "Большая ваза",
  desc: "СПбГАСУ",
  preview: "content/arch/vase2/preview/preview.png",

  viewerProfile: "arch",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "vase2",
              sourcePath: "content/arch/vase2/model/vase2.gltf",
              textures: {
                base: "content/arch/vase2/textures/BaseColor.jpg",
                normal: "content/arch/vase2/textures/Normal.jpg",
                rough: "content/arch/vase2/textures/Roughness.jpg",
                metalness: 0,
                roughness: 1,
                envIntensity: 0.75
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,
          items: [
            "content/arch/vase2/schemes/s1.jpg",
            "content/arch/vase2/schemes/s2.jpg",
            "content/arch/vase2/schemes/s3.jpg"
          ]
        },

        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,
          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/vase2/videos/scheme/v1.mp4"
          ]
        }
      }
    },

    [BLOCKS.DRAWING]: {
      subblocks: {
        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,
          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/vase2/videos/drawing/v2.mp4"
          ]
        }
      }
    }
  }
},

chair1_universal: {
  id: "chair1_universal",
  title: "Табурет квадратный",
  desc: "СПбГАСУ",
  preview: "content/arch/chair1/preview/preview.png",
  viewerProfile: "arch",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "chair1",
              sourcePath: "content/arch/chair1/model/chair1.gltf",
              textures: {
                base: "content/arch/chair1/textures/BaseColor.jpg",
                normal: "content/arch/chair1/textures/Normal.jpg",
                rough: "content/arch/chair1/textures/Roughness.jpg",
                metalness: 0,
                roughness: 1,
                envIntensity: 0.75
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,
          items: [
            "content/arch/chair1/schemes/s1.jpg",
            "content/arch/chair1/schemes/s2.jpg"
          ]
        }
      }
    },

    [BLOCKS.DRAWING]: {
      subblocks: {
        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,
          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/chair1/videos/drawing/v1.mp4"
          ]
        }
      }
    }
  }
},

chair2_universal: {
  id: "chair2_universal",
  title: "Табурет круглый",
  desc: "СПбГАСУ",
  preview: "content/arch/chair2/preview/preview.png",
  viewerProfile: "arch",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "chair2",
              sourcePath: "content/arch/chair2/model/chair2.gltf",
              textures: {
                base: "content/arch/chair2/textures/BaseColor.jpg",
                normal: "content/arch/chair2/textures/Normal.jpg",
                rough: "content/arch/chair2/textures/Roughness.jpg",
                metalness: 0,
                roughness: 1,
                envIntensity: 0.75
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,
          items: [
            "content/arch/chair2/schemes/s1.jpg",
            "content/arch/chair2/schemes/s2.jpg"
          ]
        }
      }
    },

    [BLOCKS.DRAWING]: {
      subblocks: {
        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,
          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/chair1/videos/drawing/v1.mp4"
          ]
        }
      }
    }
  }
},

ionic_universal: {
  id: "ionic_universal",
  title: "Ионическая капитель",
  desc: "СПбГАСУ",
  preview: "content/arch/ionic/preview/preview.png",
  viewerProfile: "arch",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "ionic",
              sourcePath: "content/arch/ionic/model/ionic.gltf",
              textures: {
                base: "content/arch/ionic/textures/BaseColor.jpg",
                normal: "content/arch/ionic/textures/Normal.jpg",
                rough: "content/arch/ionic/textures/Roughness.jpg",
                metalness: 0,
                roughness: 1,
                envIntensity: 0.75
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,
          items: [
            "content/arch/ionic/schemes/s1.jpg",
            "content/arch/ionic/schemes/s2.jpg",
            "content/arch/ionic/schemes/s3.jpg",
            "content/arch/ionic/schemes/s4.jpg"
          ]
        },

        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,
          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/ionic/videos/scheme/v1.mp4"
          ]
        }
      }
    }
  }
},

doric_universal: {
  id: "doric_universal",
  title: "Дорическая капитель",
  desc: "СПбГАСУ",
  preview: "content/arch/doric/preview/preview.png",
  viewerProfile: "arch",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "doric",
              sourcePath: "content/arch/doric/model/doric.gltf",
              textures: {
                base: "content/arch/doric/textures/BaseColor.jpg",
                normal: "content/arch/doric/textures/Normal.jpg",
                rough: "content/arch/doric/textures/Roughness.jpg",
                metalness: 0,
                roughness: 1,
                envIntensity: 0.75
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,
          items: [
            "content/arch/doric/schemes/s1.jpg",
            "content/arch/doric/schemes/s2.jpg",
            "content/arch/doric/schemes/s3.jpg"
          ]
        },

        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,
          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/doric/videos/scheme/v1.mp4"
          ]
        }
      }
    }
  }
},

balyasina2_universal: {
  id: "balyasina2_universal",
  title: "Балясина шаровидная",
  desc: "СПбГАСУ",
  preview: "content/arch/balyasina2/preview/preview.png",
  viewerProfile: "arch",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "balyasina2",
              sourcePath:
                "content/arch/balyasina2/model/balyasina2.gltf",

              textures: {
                base:
                  "content/arch/balyasina2/textures/BaseColor.jpg",

                normal:
                  "content/arch/balyasina2/textures/Normal.jpg",

                rough:
                  "content/arch/balyasina2/textures/Roughness.jpg",

                metalness: 0,
                roughness: 1,
                envIntensity: 0.75
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,

          items: [
            "content/arch/balyasina2/schemes/s1.jpg",
            "content/arch/balyasina2/schemes/s2.jpg",
            "content/arch/balyasina2/schemes/s3.jpg"
          ]
        },

        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,

          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/balyasina2/videos/scheme/v1.mp4"
          ]
        }
      }
    },

    [BLOCKS.DRAWING]: {
      subblocks: {
        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,

          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/balyasina2/videos/drawing/v2.mp4"
          ]
        }
      }
    }
  }
},

balyasina1_universal: {
  id: "balyasina1_universal",
  title: "Балясина с лепестками",
  desc: "СПбГАСУ",
  preview: "content/arch/balyasina1/preview/preview.png",
  viewerProfile: "arch",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "balyasina1",
              sourcePath:
                "content/arch/balyasina1/model/balyasina1.gltf",

              textures: {
                base:
                  "content/arch/balyasina1/textures/BaseColor.jpg",

                normal:
                  "content/arch/balyasina1/textures/Normal.jpg",

                rough:
                  "content/arch/balyasina1/textures/Roughness.jpg",

                metalness: 0,
                roughness: 1,
                envIntensity: 0.75
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,

          items: [
            "content/arch/balyasina1/schemes/s1.jpg",
            "content/arch/balyasina1/schemes/s2.jpg"
          ]
        },

        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,

          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/balyasina1/videos/scheme/v1.mp4"
          ]
        }
      }
    },

    [BLOCKS.DRAWING]: {
      subblocks: {
        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,

          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/balyasina1/videos/drawing/v2.mp4"
          ]
        }
      }
    }
  }
},

molbert_universal: {
  id: "molbert_universal",
  title: "Мольберт",
  desc: "СПбГАСУ",
  preview: "content/arch/molbert/preview/preview.png",
  viewerProfile: "arch",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "molbert",
              sourcePath: "content/arch/molbert/model/molbert.gltf",

              materials: {
                "1": {
                  base: "content/arch/molbert/textures/molbert2_1_BaseColor.jpg",
                  normal: "content/arch/molbert/textures/molbert2_1_Normal.jpg",
                  rough: "content/arch/molbert/textures/molbert2_1_Roughness.jpg"
                },
                "2": {
                  base: "content/arch/molbert/textures/molbert2_2_BaseColor.jpg",
                  normal: "content/arch/molbert/textures/molbert2_2_Normal.jpg",
                  rough: "content/arch/molbert/textures/molbert2_2_Roughness.jpg"
                },
                "3": {
                  base: "content/arch/molbert/textures/molbert2_3_BaseColor.jpg",
                  normal: "content/arch/molbert/textures/molbert2_3_Normal.jpg",
                  rough: "content/arch/molbert/textures/molbert2_3_Roughness.jpg"
                },
                "4": {
                  base: "content/arch/molbert/textures/molbert2_4_BaseColor.jpg",
                  normal: "content/arch/molbert/textures/molbert2_4_Normal.jpg",
                  rough: "content/arch/molbert/textures/molbert2_4_Roughness.jpg",
                  metal: "content/arch/molbert/textures/molbert2_4_Metallic.jpg"
                }
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,
          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/molbert/videos/scheme/v3.mp4"
          ]
        }
      }
    },

    [BLOCKS.DRAWING]: {
      subblocks: {
        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,
          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/molbert/videos/drawing/v1.mp4",
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/molbert/videos/drawing/v2.mp4"
          ]
        }
      }
    }
  }
},

arch_0_universal: {
  id: "arch_0_universal",
  title: "База по архитектурным деталям СПБГАСУ",
  desc: "Обзор и видеоразбор построения архитектурных деталей",
  preview: "content/arch/arch_0/preview/preview3.webp",
  viewerProfile: "base",

  blocks: {
    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,
          items: [
            "content/arch/arch_0/schemes/s1.jpg"
          ]
        }
      }
    },

    [BLOCKS.VIDEO]: {
      subblocks: {
        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,
          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/arch_0/videos/v1.mp4",
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/arch_0/videos/v2.mp4",
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/arch_0/videos/v3.mp4",
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/arch/arch_0/videos/v4.mp4"
          ]
        }
      }
    }
  }
},

inset_1_universal: {
  id: "inset_1_universal",
  title: "Куб и конус",
  desc: "Врезка прямой плоскости в тело вращения",
  preview: "content/insets/inset_1/preview/preview.png",
  viewerProfile: "inset",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "inset_1",
              sourcePath: "content/insets/inset_1/model/1.1.gltf",
              rendererSettings: {
                opacityMaterialName: "1",
                primarySectionMaterialNames: ["2", "3"],
                auxSectionMaterialNames: ["4"],
                materialColors: {
                  "2": "#d929c1",
                  "3": "#1c58e5",
                  "4": "#ddf406"
                },
                cad: {
                  fromNodes: true,
                  lines: [
                    ["a", "b"],
                    ["c", "d"]
                  ]
                }
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,
          items: [
            "content/insets/inset_1/schemes/S1.jpg",
            "content/insets/inset_1/schemes/S2.jpg",
            "content/insets/inset_1/schemes/S3.jpg",
            "content/insets/inset_1/schemes/S4.jpg",
            "content/insets/inset_1/schemes/S5.jpg"
          ]
        }
      }
    }
  }
},

inset_2_universal: {
  id: "inset_2_universal",
  title: "Горизонтальная треугольная призма и конус",
  desc: "Врезка наклонной плоскости в тело вращения",
  preview: "content/insets/inset_2/preview/preview.png",
  viewerProfile: "inset",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "inset_2",
              sourcePath: "content/insets/inset_2/model/2.3.gltf",
              rendererSettings: {
                opacityMaterialName: "1",
                primarySectionMaterialNames: ["2", "3"],
                auxSectionMaterialNames: ["4"],
                materialColors: {
                  "2": "#d929c1",
                  "3": "#1c58e5",
                  "4": "#ddf406"
                },
                cad: {
                  fromNodes: true,
                  lines: [
                    ["a", "b"],
                    ["c", "d"]
                  ]
                }
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,
          items: [
            "content/insets/inset_2/schemes/S1.jpg",
            "content/insets/inset_2/schemes/S2.jpg",
            "content/insets/inset_2/schemes/S3.jpg",
            "content/insets/inset_2/schemes/S4.jpg",
            "content/insets/inset_2/schemes/S5.jpg"
          ]
        }
      }
    }
  }
},

inset_3_universal: {
  id: "inset_3_universal",
  title: "Пирамида и горизонтальная треугольная призма",
  desc: "Врезка двух наклонных плоскостей",
  preview: "content/insets/inset_3/preview/preview.png",
  viewerProfile: "inset",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "inset_3",
              sourcePath: "content/insets/inset_3/model/3.gltf",
              rendererSettings: {
                opacityMaterialName: "1",
                primarySectionMaterialNames: ["2", "3"],
                auxSectionMaterialNames: ["4"],
                materialColors: {
                  "2": "#d929c1",
                  "3": "#1c58e5",
                  "4": "#ddf406"
                },
                cad: {
                  fromNodes: true,
                  lines: [
                    ["a", "b"]
                  ]
                }
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,
          items: [
            "content/insets/inset_3/schemes/SS1.jpg",
            "content/insets/inset_3/schemes/SS2.jpg",
            "content/insets/inset_3/schemes/SS3.jpg",
            "content/insets/inset_3/schemes/SS4.jpg",
            "content/insets/inset_3/schemes/SS5.jpg"
          ]
        }
      }
    }
  }
},

inset_4_universal: {
  id: "inset_4_universal",
  title: "Треугольная призма и шестигранник",
  desc: "Врезка двух наклонных плоскостей",
  preview: "content/insets/inset_4/preview/preview.png",
  viewerProfile: "inset",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "inset_4",
              sourcePath: "content/insets/inset_4/model/4.gltf",
              rendererSettings: {
                opacityMaterialName: "1",
                primarySectionMaterialNames: ["3"],
                auxSectionMaterialNames: ["2"],
                materialColors: {
                  "2": "#d929c1",
                  "3": "#1c58e5"
                },
                cad: {
                  fromNodes: true,
                  lines: [
                    ["a", "b"]
                  ]
                }
              }
            }
          ]
        }
      }
    }
  }
},

inset_5_universal: {
  id: "inset_5_universal",
  title: "Горизонтальный цилиндр и пирамида",
  desc: "Врезка наклонной плоскости в тело вращения",
  preview: "content/insets/inset_5/preview/preview.png",
  viewerProfile: "inset",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "inset_5",
              sourcePath: "content/insets/inset_5/model/5.1.gltf",
              rendererSettings: {
                opacityMaterialName: "1",
                primarySectionMaterialNames: ["3"],
                auxSectionMaterialNames: ["2", "4"],
                materialColors: {
                  "2": "#d929c1",
                  "3": "#1c58e5",
                  "4": "#ddf406"
                },
                cad: {
                  fromNodes: true,
                  lines: [
                    ["a", "b"],
                    ["c", "d"]
                  ]
                }
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,
          items: [
            "content/insets/inset_5/schemes/S1.jpg",
            "content/insets/inset_5/schemes/S2.jpg",
            "content/insets/inset_5/schemes/S3.jpg",
            "content/insets/inset_5/schemes/S4.jpg",
            "content/insets/inset_5/schemes/S5.jpg"
          ]
        }
      }
    }
  }
},

inset_6_universal: {
  id: "inset_6_universal",
  title: "Два горизонтальных шестигранника",
  desc: "Врезка наклонных плоскостей",
  preview: "content/insets/inset_6/preview/preview.png",
  viewerProfile: "inset",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "inset_6",
              sourcePath: "content/insets/inset_6/model/6.2.gltf",
              rendererSettings: {
                opacityMaterialName: "1",
                primarySectionMaterialNames: ["2", "3"],
                auxSectionMaterialNames: ["4", "5"],
                materialColors: {
                  "2": "#d929c1",
                  "3": "#1c58e5",
                  "4": "#ddf406",
                  "5": "#12d6c3"
                },
                cad: {
                  fromNodes: true,
                  lines: [
                    ["a", "b"],
                    ["c", "d"],
                    ["i", "j"],
                    ["k", "l"]
                  ]
                }
              }
            }
          ]
        }
      }
    }
  }
},

inset_7_universal: {
  id: "inset_7_universal",
  title: "Горизонтальный цилиндр и треугольная призма",
  desc: "Врезка наклонной плоскости в тело вращения",
  preview: "content/insets/inset_7/preview/preview.png",
  viewerProfile: "inset",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "inset_7",
              sourcePath: "content/insets/inset_7/model/7.1.gltf",
              rendererSettings: {
                opacityMaterialName: "1",
                primarySectionMaterialNames: ["2", "3"],
                auxSectionMaterialNames: ["4"],
                materialColors: {
                  "2": "#d929c1",
                  "3": "#1c58e5",
                  "4": "#ddf406"
                },
                cad: {
                  fromNodes: true,
                  lines: [
                    ["a", "b"],
                    ["c", "d"]
                  ]
                }
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,
          items: [
            "content/insets/inset_7/schemes/S1.jpg",
            "content/insets/inset_7/schemes/S2.jpg",
            "content/insets/inset_7/schemes/S3.jpg",
            "content/insets/inset_7/schemes/S4.jpg",
            "content/insets/inset_7/schemes/S5.jpg"
          ]
        }
      }
    }
  }
},

inset_8_universal: {
  id: "inset_8_universal",
  title: "Вертикальная треугольная призма и цилиндр",
  desc: "Врезка наклонной плоскости в тело вращения",
  preview: "content/insets/inset_8/preview/preview.png",
  viewerProfile: "inset",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "inset_8",
              sourcePath: "content/insets/inset_8/model/8.gltf",
              rendererSettings: {
                opacityMaterialName: "1",
                primarySectionMaterialNames: ["3"],
                auxSectionMaterialNames: ["2", "4"],
                materialColors: {
                  "2": "#d929c1",
                  "3": "#1c58e5",
                  "4": "#ddf406"
                },
                cad: {
                  fromNodes: true,
                  lines: [
                    ["a", "b"],
                    ["c", "d"]
                  ]
                }
              }
            }
          ]
        }
      }
    }
  }
},

inset_9_universal: {
  id: "inset_9_universal",
  title: "Вертикальный цилиндр и треугольная призма",
  desc: "Врезка наклонной плоскости в тело вращения",
  preview: "content/insets/inset_9/preview/preview.png",
  viewerProfile: "inset",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "inset_9",
              sourcePath: "content/insets/inset_9/model/9.gltf",
              rendererSettings: {
                opacityMaterialName: "1",
                primarySectionMaterialNames: ["3"],
                auxSectionMaterialNames: ["2", "4"],
                materialColors: {
                  "2": "#d929c1",
                  "3": "#1c58e5",
                  "4": "#ddf406"
                },
                cad: {
                  fromNodes: true,
                  lines: [
                    ["a", "b"],
                    ["c", "d"]
                  ]
                }
              }
            }
          ]
        }
      }
    },

    [BLOCKS.SCHEMES]: {
      subblocks: {
        [SUBBLOCKS.SCHEMES]: {
          type: CONTENT_TYPES.IMAGES,
          items: [
            "content/insets/inset_9/schemes/S1.jpg",
            "content/insets/inset_9/schemes/S2.jpg",
            "content/insets/inset_9/schemes/S3.jpg",
            "content/insets/inset_9/schemes/S4.jpg",
            "content/insets/inset_9/schemes/S5.jpg"
          ]
        }
      }
    }
  }
},

inset_10_universal: {
  id: "inset_10_universal",
  title: "Горизонтальный цилиндр и шестигранник",
  desc: "Врезка наклонной плоскости в тело вращения",
  preview: "content/insets/inset_10/preview/preview.png",
  viewerProfile: "inset",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "inset_10",
              sourcePath: "content/insets/inset_10/model/10.gltf",
              rendererSettings: {
                opacityMaterialName: "1",
                primarySectionMaterialNames: ["2", "3"],
                auxSectionMaterialNames: ["4", "5"],
                materialColors: {
                  "2": "#d929c1",
                  "3": "#1c58e5",
                  "4": "#ddf406",
                  "5": "#12d6c3"
                },
                cad: {
                  fromNodes: true,
                  lines: [
                    ["a", "b"],
                    ["c", "d"],
                    ["e", "f"],
                    ["g", "h"]
                  ]
                }
              }
            }
          ]
        }
      }
    }
  }
},

inset_11_universal: {
  id: "inset_11_universal",
  title: "Вертикальный цилиндр и пирамида",
  desc: "Врезка наклонной плоскости в тело вращения",
  preview: "content/insets/inset_11/preview/preview.png",
  viewerProfile: "inset",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "inset_11",
              sourcePath: "content/insets/inset_11/model/11.gltf",
              rendererSettings: {
                opacityMaterialName: "1",
                primarySectionMaterialNames: ["2", "3"],
                auxSectionMaterialNames: ["4", "5", "6"],
                materialColors: {
                  "2": "#d929c1",
                  "3": "#1c58e5",
                  "4": "#ddf406",
                  "5": "#12d6c3",
                  "6": "#FF5500"
                },
                cad: {
                  fromNodes: true,
                  lines: [
                    ["a", "b"],
                    ["c", "d"],
                    ["e", "f"],
                    ["g", "h"]
                  ]
                }
              }
            }
          ]
        }
      }
    }
  }
},

inset_12_universal: {
  id: "inset_12_universal",
  title: "Пирамида и горизонтальный шестигранник",
  desc: "Врезка наклонных плоскостей",
  preview: "content/insets/inset_12/preview/preview.png",
  viewerProfile: "inset",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "inset_12",
              sourcePath: "content/insets/inset_12/model/12.gltf",
              rendererSettings: {
                opacityMaterialName: "1",
                primarySectionMaterialNames: ["3"],
                auxSectionMaterialNames: ["2", "4"],
                materialColors: {
                  "2": "#d929c1",
                  "3": "#1c58e5",
                  "4": "#ddf406"
                },
                cad: {
                  fromNodes: true,
                  lines: [
                    ["a", "b"],
                    ["c", "d"]
                  ]
                }
              }
            }
          ]
        }
      }
    }
  }
},

inset_13_universal: {
  id: "inset_13_universal",
  title: "Вертикальная пирамида и треугольная призма",
  desc: "Врезка наклонных плоскостей",
  preview: "content/insets/inset_13/preview/preview.png",
  viewerProfile: "inset",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "inset_13",
              sourcePath: "content/insets/inset_13/model/13.gltf",
              rendererSettings: {
                opacityMaterialName: "1",
                primarySectionMaterialNames: ["2", "3"],
                materialColors: {
                  "2": "#d929c1",
                  "3": "#1c58e5"
                },
                cad: {
                  fromNodes: true,
                  lines: [
                    ["a", "b"],
                    ["c", "d"]
                  ]
                }
              }
            }
          ]
        }
      }
    }
  }
},

inset_14_universal: {
  id: "inset_14_universal",
  title: "Вертикальная треугольная призма и конус",
  desc: "Врезка наклонной плоскости в тело вращения",
  preview: "content/insets/inset_14/preview/preview.png",
  viewerProfile: "inset",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "inset_14",
              sourcePath: "content/insets/inset_14/model/14.gltf",
              rendererSettings: {
                opacityMaterialName: "1",
                primarySectionMaterialNames: ["2", "5"],
                auxSectionMaterialNames: ["3", "4"],
                materialColors: {
                  "2": "#1c58e5",
                  "3": "#ddf406",
                  "4": "#12d6c3",
                  "5": "#d929c1"
                },
                cad: {
                  fromNodes: true,
                  lines: [
                    ["a", "b"],
                    ["c", "d"]
                  ]
                }
              }
            }
          ]
        }
      }
    }
  }
},

inset_0_universal: {
  id: "inset_0_universal",
  title: "Базовая информация про врезки",
  desc: "Общая теория построения любой врезки",
  preview: "content/insets/inset_0/preview/preview4.webp",

  viewerProfile: "base",

  blocks: {
    [BLOCKS.VIDEO]: {
      subblocks: {
        [SUBBLOCKS.VIDEOS]: {
          type: CONTENT_TYPES.VIDEOS,

          items: [
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/insets/inset_0/videos/v2.mp4",
            "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/content/insets/inset_0/videos/v5.mp4"
          ]
        }
      }
    }
  }
},
  auditorium_1_location_2: {
  id: "auditorium_1_location_2",
  title: "Локация 2",
  preview: "content/rooms/auditorium_1/location_2/preview/preview.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_1_location_2",
              sourcePath: "content/rooms/auditorium_1/location_2/model/location_2.glb",
              textures: {
                base: "content/rooms/auditorium_1/location_2/textures/1.jpg",
                roughness: 1
              }
            }
          ]
        }
      }
    }
  }
},
  auditorium_1_location_1: {
  id: "auditorium_1_location_1",
  title: "Локация 1",
  preview: "content/rooms/auditorium_1/location_1/preview/preview.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_1_location_1",
              sourcePath: "content/rooms/auditorium_1/location_1/model/location_1.glb",
              texturesDir: "content/rooms/auditorium_1/location_1/textures/"
            }
          ]
        }
      }
    }
  }
},
  auditorium_1_location_3: {
  id: "auditorium_1_location_3",
  title: "Локация 3",
  preview: "content/rooms/auditorium_1/location_3/preview/preview.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_1_location_3",
              sourcePath: "content/rooms/auditorium_1/location_3/model/location_3.glb",
              texturesDir: "content/rooms/auditorium_1/location_3/textures/"
            }
          ]
        }
      }
    }
  }
},
  auditorium_2_location_4: {
  id: "auditorium_2_location_4",
  title: "Локация 4",
  preview: "content/rooms/auditorium_2/location_4/preview/preview.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_2_location_4",
              sourcePath: "content/rooms/auditorium_2/location_4/model/location_4.glb",
              texturesDir: "content/rooms/auditorium_2/location_4/textures/"
            }
          ]
        }
      }
    }
  }
},
  auditorium_2_location_5: {
  id: "auditorium_2_location_5",
  title: "Локация 5",
  preview: "content/rooms/auditorium_2/location_5/preview/preview.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_2_location_5",
              sourcePath: "content/rooms/auditorium_2/location_5/model/location_5.1.glb",
              texturesDir: "content/rooms/auditorium_2/location_5/textures/"
            }
          ]
        }
      }
    }
  }
},
  auditorium_3_location_7: {
  id: "auditorium_3_location_7",
  title: "Локация 7",
  preview: "content/rooms/auditorium_3/location_7/preview/preview.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_3_location_7",
              sourcePath: "content/rooms/auditorium_3/location_7/model/location_7.1.glb",
              texturesDir: "content/rooms/auditorium_3/location_7/textures/"
            }
          ]
        }
      }
    }
  }
},
  auditorium_2_location_6: {
  id: "auditorium_2_location_6",
  title: "Локация 6",
  preview: "content/rooms/auditorium_2/location_6/preview/preview.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_2_location_6",
              sourcePath: "content/rooms/auditorium_2/location_6/model/location_6.glb",
              texturesDir: "content/rooms/auditorium_2/location_6/textures/"
            }
          ]
        }
      }
    }
  }
},
  auditorium_3_location_8: {
  id: "auditorium_3_location_8",
  title: "Локация 8",
  preview: "content/rooms/auditorium_3/location_8/preview/preview2.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_3_location_8",
              sourcePath: "content/rooms/auditorium_3/location_8/model/location_8.glb",
              texturesDir: "content/rooms/auditorium_3/location_8/textures/"
            }
          ]
        }
      }
    }
  }
},
  auditorium_3_location_9: {
  id: "auditorium_3_location_9",
  title: "Локация 9",
  preview: "content/rooms/auditorium_3/location_9/preview/preview.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_3_location_9",
              sourcePath: "content/rooms/auditorium_3/location_9/model/location_9.glb",
              texturesDir: "content/rooms/auditorium_3/location_9/textures/"
            }
          ]
        }
      }
    }
  }
},
  auditorium_4_location_10: {
  id: "auditorium_4_location_10",
  title: "Локация 10",
  preview: "content/rooms/auditorium_4/location_10/preview/preview.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_4_location_10",
              sourcePath: "content/rooms/auditorium_4/location_10/model/location_10.glb",
              texturesDir: "content/rooms/auditorium_4/location_10/textures/"
            }
          ]
        }
      }
    }
  }
},
  auditorium_4_location_11: {
  id: "auditorium_4_location_11",
  title: "Локация 11",
  preview: "content/rooms/auditorium_4/location_11/preview/preview.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_4_location_11",
              sourcePath: "content/rooms/auditorium_4/location_11/model/location_11.glb",
              texturesDir: "content/rooms/auditorium_4/location_11/textures/"
            }
          ]
        }
      }
    }
  }
},
  auditorium_4_location_12: {
  id: "auditorium_4_location_12",
  title: "Локация 12",
  preview: "content/rooms/auditorium_4/location_12/preview/preview.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_4_location_12",
              sourcePath: "content/rooms/auditorium_4/location_12/model/location_12.glb",
              texturesDir: "content/rooms/auditorium_4/location_12/textures/"
            }
          ]
        }
      }
    }
  }
},
  auditorium_5_location_13: {
  id: "auditorium_5_location_13",
  title: "Локация 13",
  preview: "content/rooms/auditorium_5/location_13/preview/preview.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_5_location_13",
              sourcePath: "content/rooms/auditorium_5/location_13/model/location_13.glb",
              texturesDir: "content/rooms/auditorium_5/location_13/textures/"
            }
          ]
        }
      }
    }
  }
},
  auditorium_5_location_14: {
  id: "auditorium_5_location_14",
  title: "Локация 14",
  preview: "content/rooms/auditorium_5/location_14/preview/preview.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_5_location_14",
              sourcePath: "content/rooms/auditorium_5/location_14/model/location_14.glb",
              texturesDir: "content/rooms/auditorium_5/location_14/textures/"
            }
          ]
        }
      }
    }
  }
},
  auditorium_6_location_15: {
  id: "auditorium_6_location_15",
  title: "Локация 15",
  preview: "content/rooms/auditorium_6/location_15/preview/preview.png",

  viewerProfile: "rooms",

  blocks: {
    [BLOCKS.MODEL_3D]: {
      subblocks: {
        [SUBBLOCKS.MODEL_3D]: {
          type: CONTENT_TYPES.MODEL,
          items: [
            {
              id: "auditorium_6_location_15",
              sourcePath: "content/rooms/auditorium_6/location_15/model/location_15.glb",
              texturesDir: "content/rooms/auditorium_6/location_15/textures/"
            }
          ]
        }
      }
    }
  }
}
};
